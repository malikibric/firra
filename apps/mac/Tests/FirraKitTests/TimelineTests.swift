import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct TimelineTests {
  private let detail = ClientDetailTests().detail
  private let emptyEvidence = #"{"items":[],"next_cursor":null}"#

  private func page(_ ids: [String], cursor: String? = nil) -> String {
    let items = ids.map {
      #"{"id":"\#($0)","occurred_at":"2026-10-01","kind":"signal","type":"delay","evidence_ids":["cached","missing","gone"]}"#
    }.joined(separator: ",")
    let next = cursor.map { "\"\($0)\"" } ?? "null"
    return #"{"items":[\#(items)],"next_cursor":\#(next)}"#
  }

  private func setup(_ replies: [StubTransport.Reply], cursor: String? = "opaque+/=") -> (ClientDetailModel, StubTransport) {
    let stub = StubTransport([
      .init(status: 200, body: detail), .init(status: 200, body: page(["new"], cursor: cursor)),
      .init(status: 200, body: emptyEvidence),
    ] + replies)
    let api = APIClient(base: testBaseURL, transport: stub, tokenProvider: { "tok" })
    return (ClientDetailModel(api: api), stub)
  }

  private func cursor(_ request: URLRequest) -> String? {
    URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "cursor" }?.value
  }

  @Test func pagesInOrderDeduplicatesAndPassesOpaqueCursors() async {
    let (model, stub) = setup([
      .init(status: 200, body: page(["new", "older", "older"], cursor: "second")),
      .init(status: 200, body: page(["oldest"])),
    ])
    await model.load(workspaceId: "w", clientId: "c1")
    #expect(model.timeline.map(\.id) == ["new"] && model.canLoadMoreTimeline)
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    #expect(model.timeline.map(\.id) == ["new", "older"] && model.canLoadMoreTimeline)
    #expect(cursor(stub.requests[3]) == "opaque+/=")
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    #expect(cursor(stub.requests[4]) == "second")
    #expect(model.timeline.map(\.id) == ["new", "older", "oldest"])
    #expect(!model.canLoadMoreTimeline && !model.isLoadingTimeline)
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    #expect(stub.requests.count == 5)
  }

  @Test func noCursorMakesPagingANoOp() async {
    let (model, stub) = setup([], cursor: nil)
    await model.load(workspaceId: "w", clientId: "c1")
    #expect(!model.canLoadMoreTimeline)
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    #expect(stub.requests.count == 3)
  }

  @Test func failedOlderPageKeepsEventsAndCursorForRetry() async {
    let (model, stub) = setup([
      .init(status: 500, body: "{}"), .init(status: 200, body: page(["older"])),
    ])
    await model.load(workspaceId: "w", clientId: "c1")
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    #expect(model.timeline.map(\.id) == ["new"])
    #expect(model.timelineFailed && model.canLoadMoreTimeline && !model.isLoadingTimeline)
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    #expect(cursor(stub.requests[3]) == cursor(stub.requests[4]))
    #expect(model.timeline.map(\.id) == ["new", "older"] && !model.timelineFailed)
  }

  @Test func resetClearsEverythingAndLoadStartsWithoutACursor() async {
    let (model, stub) = setup([
      .init(status: 500, body: "{}"),
      .init(status: 200, body: detail), .init(status: 200, body: page(["other"])),
      .init(status: 200, body: emptyEvidence),
    ])
    await model.load(workspaceId: "w", clientId: "c1")
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    model.reset()
    #expect(model.phase == .idle && model.detail == nil && model.message == nil)
    #expect(model.timeline.isEmpty && model.evidence.isEmpty)
    #expect(!model.canLoadMoreTimeline && !model.timelineFailed && !model.isLoadingTimeline)
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    #expect(stub.requests.count == 4)
    await model.load(workspaceId: "w", clientId: "c1")
    #expect(cursor(stub.requests[5]) == nil && !model.canLoadMoreTimeline)
    #expect(model.timeline.map(\.id) == ["other"])
  }

  @Test func concurrentPagingIsIgnoredAndResetDiscardsAnInFlightPage() async {
    let transport = TimelineGatedTransport(initial: [
      .init(status: 200, body: detail), .init(status: 200, body: page(["new"], cursor: "older")),
      .init(status: 200, body: emptyEvidence),
    ], older: .init(status: 200, body: page(["old"])))
    let model = ClientDetailModel(api: APIClient(base: testBaseURL, transport: transport, tokenProvider: { "tok" }))
    await model.load(workspaceId: "w", clientId: "c1")
    let loading = Task { await model.loadMoreTimeline(workspaceId: "w", clientId: "c1") }
    await transport.older.waitForRequests(1)
    #expect(model.isLoadingTimeline)
    await model.loadMoreTimeline(workspaceId: "w", clientId: "c1")
    #expect(transport.older.requestCount == 1)
    model.reset()
    transport.older.releaseAll()
    await loading.value
    #expect(model.timeline.isEmpty && !model.canLoadMoreTimeline && !model.isLoadingTimeline)
    #expect(!model.timelineFailed && model.phase == .idle)
  }

  @Test func quotesReuseTheCacheAndFetchMissingEvidenceOnlyOnce() async throws {
    let cached = #"{"id":"cached","quote":"Already here.","occurred_at":"2026-10-01"}"#
    let missing = #"{"id":"missing","quote":"Exact quote — déjà vu.","occurred_at":"2026-09-30"}"#
    let stub = StubTransport([
      .init(status: 200, body: detail), .init(status: 200, body: page(["signal"])),
      .init(status: 200, body: #"{"items":[\#(cached)],"next_cursor":null}"#),
      .init(status: 200, body: missing), .init(status: 404, body: "{}"),
    ])
    let model = ClientDetailModel(api: APIClient(base: testBaseURL, transport: stub, tokenProvider: { "tok" }))
    await model.load(workspaceId: "w", clientId: "c1")
    let event = try #require(model.timeline.first)
    await model.loadTimelineEvidence(workspaceId: "w", event: event)
    await model.loadTimelineEvidence(workspaceId: "w", event: event)
    #expect(model.quotes(for: event).map(\.quote) == ["Already here.", "Exact quote — déjà vu."])
    #expect(model.evidence["missing"]?.quote == "Exact quote — déjà vu.")
    #expect(stub.requests.count == 5)
    #expect(stub.requests[3].url?.path == "/v1/workspaces/w/evidence/missing")
    #expect(stub.requests[4].url?.path == "/v1/workspaces/w/evidence/gone")
    let metadata = TimelineEvent(id: "metadata", occurredAt: "x", kind: "signal", from: nil, to: nil,
      type: nil, change: nil, severity: nil, ownerSide: nil, status: nil, event: nil, evidenceIds: nil)
    await model.loadTimelineEvidence(workspaceId: "w", event: metadata)
    #expect(model.quotes(for: metadata).isEmpty && stub.requests.count == 5)
    model.reset()
    #expect(model.evidence.isEmpty && model.quotes(for: event).isEmpty)
  }
}

private final class TimelineGatedTransport: HTTPTransport, Sendable {
  let initial: StubTransport
  let older: GatedTransport

  init(initial: [StubTransport.Reply], older: StubTransport.Reply) {
    self.initial = StubTransport(initial)
    self.older = GatedTransport(reply: older)
  }

  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
    if query.contains(where: { $0.name == "cursor" }) { return try await older.send(request) }
    return try await initial.send(request)
  }
}
