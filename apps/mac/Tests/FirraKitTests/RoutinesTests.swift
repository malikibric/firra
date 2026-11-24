import Foundation
import Testing
@testable import FirraKit

private func routinesJSON(enabled: Bool = true, status: String? = nil) -> String {
  let run = status.map {
    #"{"scheduled_for":"2026-10-03","status":"\#($0)","finished_at":null,"counts":{"cards":2}}"#
  } ?? "null"
  return #"{"generated_at":"2026-10-03T08:00:00Z","items":[{"kind":"morning_scan","enabled":\#(enabled),"created":false,"last_run":\#(run)},{"kind":"commitment_watch","enabled":true,"created":true,"last_run":null},{"kind":"pre_meeting_brief","enabled":true,"created":false,"last_run":null}]}"#
}

@MainActor @Suite struct RoutinesTests {
  private func model(_ transport: any HTTPTransport) -> RoutinesModel {
    RoutinesModel(api: APIClient(base: testBaseURL, transport: transport, tokenProvider: { "tok" }))
  }

  @Test func decodesAllKindsDefaultsNullsAndUnknownStatus() throws {
    let response = try JSONDecoder.api.decode(RoutinesResponse.self, from: Data(routinesJSON(status: "future_status").utf8))
    #expect(response.items.map(\.kind) == RoutineKind.allCases)
    #expect(response.items[0].enabled && !response.items[0].created)
    #expect(response.items[0].lastRun?.finishedAt == nil)
    #expect(response.items[0].lastRun?.counts == ["cards": 2])
    #expect(response.items[0].lastRun?.displayStatus == "unknown")
    #expect(response.items[1].lastRun == nil && response.items[2].lastRun == nil)
    for status in ["queued", "running", "succeeded", "failed"] {
      let json = routinesJSON(status: status).replacingOccurrences(of: #""finished_at":null"#, with: #""finished_at":"2026-10-03T08:01:00Z""#)
      let decoded = try JSONDecoder.api.decode(RoutinesResponse.self, from: Data(json.utf8))
      #expect(decoded.items[0].lastRun?.displayStatus == status)
      #expect(decoded.items[0].lastRun?.finishedAt == "2026-10-03T08:01:00Z")
    }
  }

  @Test func exactRequestsAndSuccessfulWriteRefresh() async throws {
    let stub = StubTransport([
      .init(status: 200, body: routinesJSON()), .init(status: 200, body: #"{"id":"r1"}"#),
      .init(status: 200, body: routinesJSON(enabled: false)),
    ])
    let model = model(stub)
    await model.load(workspaceId: "w")
    #expect(model.canEdit && model.items.count == 3)
    await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false)
    #expect(model.items[0].enabled == false && model.canEdit && model.message == nil)
    #expect(stub.requests.map(\.httpMethod) == ["GET", "PUT", "GET"])
    #expect(stub.requests[0].url?.path == "/v1/workspaces/w/routines")
    #expect(stub.requests[1].url?.path == "/v1/workspaces/w/routines/morning_scan")
    let data = try #require(stub.requests[1].httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Bool])
    #expect(body == ["enabled": false])
    #expect(stub.requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == "Bearer tok" })
    #expect(stub.requests[1].value(forHTTPHeaderField: "Content-Type") == "application/json")
  }

  @Test func allKindsUseTheirExactWritePathAndDecodeTheIdResponse() async throws {
    let stub = StubTransport(RoutineKind.allCases.map { _ in .init(status: 200, body: #"{"id":"r"}"#) })
    let api = APIClient(base: testBaseURL, transport: stub, tokenProvider: { "tok" })
    for kind in RoutineKind.allCases { try await api.setRoutineEnabled(workspaceId: "w", kind: kind, enabled: true) }
    #expect(stub.requests.map { $0.url!.path } == RoutineKind.allCases.map { "/v1/workspaces/w/routines/\($0.rawValue)" })
    for request in stub.requests {
      let data = try #require(request.httpBody)
      let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Bool])
      #expect(request.httpMethod == "PUT" && body == ["enabled": true])
    }
  }

  @Test func initialReadFailureHasNoGuessedSwitchesAndRetries() async {
    let stub = StubTransport([.init(status: 500, body: "{}"), .init(status: 200, body: routinesJSON())])
    let model = model(stub)
    await model.load(workspaceId: "w")
    if case .failed = model.phase {} else { Issue.record("Expected read failure") }
    #expect(model.items.isEmpty && !model.canEdit)
    await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false)
    #expect(stub.requests.count == 1)
    await model.load(workspaceId: "w")
    #expect(model.items.count == 3 && model.canEdit)
  }

  @Test func refusedWriteKeepsConfirmedValueAndCanBeRetried() async {
    let stub = StubTransport([
      .init(status: 200, body: routinesJSON()),
      .init(status: 422, body: #"{"error":"invalid_request","message":"Invalid request.","request_id":"r"}"#),
      .init(status: 200, body: #"{"id":"r1"}"#), .init(status: 200, body: routinesJSON(enabled: false)),
    ])
    let model = model(stub)
    await model.load(workspaceId: "w")
    await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false)
    #expect(model.items[0].enabled && model.message != nil && model.canEdit)
    await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false)
    #expect(!model.items[0].enabled && model.message == nil)
  }

  @Test func offlineWriteKeepsValueAndIsNeverQueued() async {
    let stub = StubTransport([.init(status: 200, body: routinesJSON()), .init(status: 200, body: routinesJSON())])
    let model = model(RoutinesOfflineTransport(initial: stub))
    await model.load(workspaceId: "w")
    await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false)
    #expect(model.items[0].enabled && model.message?.contains("Restart the engine") == true)
    await model.load(workspaceId: "w")
    #expect(stub.requests.count == 2 && stub.requests.allSatisfy { $0.httpMethod == "GET" })
    #expect(model.items[0].enabled)
  }

  @Test func failedRefreshKeepsSuccessfulWriteReadOnlyUntilRetry() async {
    let stub = StubTransport([
      .init(status: 200, body: routinesJSON()), .init(status: 200, body: #"{"id":"r1"}"#),
      .init(status: 500, body: "{}"), .init(status: 200, body: routinesJSON(enabled: false)),
    ])
    let model = model(stub)
    await model.load(workspaceId: "w")
    await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false)
    #expect(!model.items[0].enabled && model.isStale && !model.canEdit && model.message != nil)
    await model.setEnabled(workspaceId: "w", kind: .commitmentWatch, enabled: false)
    #expect(stub.requests.count == 3)
    await model.load(workspaceId: "w")
    #expect(!model.isStale && model.canEdit && !model.items[0].enabled)
  }

  @Test func refreshFailureWithoutWriteAlsoMakesSettingsStale() async {
    let stub = StubTransport([.init(status: 200, body: routinesJSON()), .init(status: 500, body: "{}")])
    let model = model(stub)
    await model.load(workspaceId: "w")
    await model.load(workspaceId: "w")
    #expect(model.items.count == 3 && model.isStale && !model.canEdit)
  }

  @Test func writesAreSerializedAndDoubleTapCannotChangeTheUnconfirmedValue() async {
    let transport = RoutinesGatedTransport(
      replies: [.init(status: 200, body: routinesJSON()), .init(status: 200, body: routinesJSON(enabled: false))],
      gate: { $0.httpMethod == "PUT" }, reply: .init(status: 200, body: #"{"id":"r1"}"#))
    let model = model(transport)
    await model.load(workspaceId: "w")
    let writing = Task { await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false) }
    await transport.gated.waitForRequests(1)
    #expect(model.items[0].enabled && model.saving == .morningScan && !model.canEdit)
    await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false)
    await model.setEnabled(workspaceId: "w", kind: .commitmentWatch, enabled: false)
    await model.load(workspaceId: "w")
    #expect(transport.gated.requestCount == 1 && transport.initial.requests.count == 1)
    transport.gated.releaseAll()
    await writing.value
    #expect(!model.items[0].enabled && model.canEdit)
  }

  @Test func workspaceChangeClearsStateAndIgnoresLateRead() async {
    let transport = RoutinesGatedTransport(
      replies: [.init(status: 500, body: "{}")], gate: { $0.url?.path == "/v1/workspaces/old/routines" },
      reply: .init(status: 200, body: routinesJSON()))
    let model = model(transport)
    let loading = Task { await model.load(workspaceId: "old") }
    await transport.gated.waitForRequests(1)
    await model.load(workspaceId: "new")
    #expect(model.items.isEmpty && model.workspaceId == "new")
    transport.gated.releaseAll()
    await loading.value
    #expect(model.items.isEmpty && !model.canEdit)
    if case .failed = model.phase {} else { Issue.record("Late read replaced the error state") }
  }

  @Test func workspaceChangeIgnoresLateWriteAndDoesNotRefreshOldWorkspace() async {
    let transport = RoutinesGatedTransport(
      replies: [.init(status: 200, body: routinesJSON()), .init(status: 200, body: routinesJSON())],
      gate: { $0.httpMethod == "PUT" }, reply: .init(status: 200, body: #"{"id":"r1"}"#))
    let model = model(transport)
    await model.load(workspaceId: "old")
    let writing = Task { await model.setEnabled(workspaceId: "old", kind: .morningScan, enabled: false) }
    await transport.gated.waitForRequests(1)
    await model.load(workspaceId: "new")
    #expect(!model.canEdit)
    transport.gated.releaseAll()
    await writing.value
    #expect(model.workspaceId == "new" && model.items[0].enabled && model.canEdit)
    #expect(transport.initial.requests.count == 2)
  }

  @Test func anOlderRefreshCannotOverwriteANewerConfirmedWrite() async {
    let transport = RoutinesGatedTransport(
      replies: [.init(status: 200, body: routinesJSON()), .init(status: 200, body: #"{"id":"r1"}"#),
        .init(status: 200, body: routinesJSON(enabled: false))],
      gate: { $0.value(forHTTPHeaderField: "Authorization") == "Bearer old" },
      reply: .init(status: 200, body: routinesJSON()))
    let tokens = RoutineTokens()
    let model = RoutinesModel(api: APIClient(base: testBaseURL, transport: transport, tokenProvider: { await tokens.next() }))
    let oldRead = Task { await model.load(workspaceId: "w") }
    await transport.gated.waitForRequests(1)
    await model.load(workspaceId: "w")
    await model.setEnabled(workspaceId: "w", kind: .morningScan, enabled: false)
    transport.gated.releaseAll()
    await oldRead.value
    #expect(!model.items[0].enabled && model.canEdit)
  }

  @Test func accessFailureUsesAccessMessage() async {
    let stub = StubTransport([.init(status: 403, body: #"{"error":"forbidden","message":"Forbidden.","request_id":"r"}"#)])
    let model = model(stub)
    await model.load(workspaceId: "w")
    if case .failed(let message) = model.phase { #expect(message.contains("do not have access")) }
    else { Issue.record("Expected access failure") }
  }

  @Test func unauthorizedReadUsesExistingSignOutCallback() async {
    let calls = RoutineTokens()
    let stub = StubTransport([.init(status: 401, body: "{}")])
    let api = APIClient(base: testBaseURL, transport: stub, tokenProvider: { "tok" }, onUnauthorized: { await calls.called() })
    let model = RoutinesModel(api: api)
    await model.load(workspaceId: "w")
    #expect(await calls.count == 1 && model.items.isEmpty)
  }
}

private final class RoutinesGatedTransport: HTTPTransport, Sendable {
  let initial: StubTransport
  let gated: GatedTransport
  let gate: @Sendable (URLRequest) -> Bool
  init(replies: [StubTransport.Reply], gate: @escaping @Sendable (URLRequest) -> Bool, reply: StubTransport.Reply) {
    initial = StubTransport(replies)
    gated = GatedTransport(reply: reply)
    self.gate = gate
  }
  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    try await gate(request) ? gated.send(request) : initial.send(request)
  }
}

private struct RoutinesOfflineTransport: HTTPTransport {
  let initial: StubTransport
  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    if request.httpMethod == "PUT" { throw URLError(.notConnectedToInternet) }
    return try await initial.send(request)
  }
}

private actor RoutineTokens {
  var count = 0
  func next() -> String { count += 1; return count == 1 ? "old" : "tok" }
  func called() { count += 1 }
}
