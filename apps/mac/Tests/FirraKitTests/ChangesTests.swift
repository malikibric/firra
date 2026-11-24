import Foundation
import Testing
@testable import FirraKit

private func change(_ kind: String, day: String = "2026-10-03", client: String = "c1") -> String {
  #"{"client_id":"\#(client)","client_name":"Example","as_of":"\#(day)","kind":"\#(kind)","from":"watch","to":"elevated","signal_type":"reduced_engagement","family":"engagement","severity":"medium","evidence_count":0}"#
}

private func changesBody(_ items: [String]) -> String {
  #"{"generated_at":"2026-10-03T12:00:00Z","since":"2026-09-26","items":[\#(items.joined(separator: ","))]}"#
}

@MainActor @Suite struct ChangesTests {
  private func api(_ transport: any HTTPTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: transport, tokenProvider: { "tok" })
  }

  private func model(_ transport: any HTTPTransport) -> ChangesModel {
    ChangesModel(api: api(transport), now: { Date(timeIntervalSince1970: 1_791_028_800) })
  }

  @Test func decodesEveryKindAndAnUnknownKindWithReadableLines() throws {
    let kinds = ["risk_state_changed", "expansion_state_changed", "signal_opened", "signal_escalated", "signal_resolved", "future_kind"]
    let response = try JSONDecoder.api.decode(ChangesResponse.self, from: Data(changesBody(kinds.map { change($0) }).utf8))
    #expect(response.generatedAt == "2026-10-03T12:00:00Z" && response.since == "2026-09-26")
    #expect(response.items.map(\.kind) == kinds)
    #expect(response.items.map(\.summary) == ["Risk watch → elevated", "Expansion watch → elevated",
      "Reduced Engagement signal opened (medium)", "Reduced Engagement signal escalated (medium)",
      "Reduced Engagement signal resolved", "Change recorded"])
    #expect(response.items[0].clientId == "c1" && response.items[0].clientName == "Example")
    #expect(response.items[0].family == "engagement" && response.items[0].evidenceCount == 0)
    let nullable = #"{"client_id":"c2","client_name":"Metadata","as_of":"2026-10-02","kind":"signal_resolved","from":null,"to":null,"signal_type":"delay","family":null,"severity":null,"evidence_count":0}"#
    let item = try JSONDecoder.api.decode(ChangeItem.self, from: Data(nullable.utf8))
    #expect(item.from == nil && item.to == nil && item.family == nil && item.severity == nil)
    #expect(item.summary == "Delay signal resolved")
  }

  @Test func requestUsesThePathAndOptionalSince() async throws {
    let stub = StubTransport(Array(repeating: .init(status: 200, body: changesBody([])), count: 2))
    let client = api(stub)
    _ = try await client.changes(workspaceId: "w1", since: "2026-09-26")
    _ = try await client.changes(workspaceId: "w1")
    #expect(stub.requests[0].httpMethod == "GET")
    #expect(stub.requests[0].url?.path == "/v1/workspaces/w1/changes")
    #expect(URLComponents(url: stub.requests[0].url!, resolvingAgainstBaseURL: false)?.queryItems == [URLQueryItem(name: "since", value: "2026-09-26")])
    #expect(URLComponents(url: stub.requests[1].url!, resolvingAgainstBaseURL: false)?.queryItems?.isEmpty != false)
  }

  @Test func groupsDaysAndPreservesTheServerOrder() async {
    let stub = StubTransport([.init(status: 200, body: changesBody([
      change("signal_opened"), change("signal_resolved", client: "c2"),
      change("risk_state_changed", day: "2026-10-02"),
    ]))])
    let model = model(stub)
    await model.load(workspaceId: "w1")
    #expect(model.phase == .ready && model.windowDays == 7)
    #expect(model.groupedByDay.map(\.day) == ["2026-10-03", "2026-10-02"])
    #expect(model.groupedByDay[0].items.map(\.clientId) == ["c1", "c2"])
    #expect(model.items.map(\.kind) == ["signal_opened", "signal_resolved", "risk_state_changed"])
  }

  @Test func changingTheWindowReloadsAndRejectsUnsupportedChoices() async {
    let stub = StubTransport(Array(repeating: .init(status: 200, body: changesBody([])), count: 3))
    let model = model(stub)
    await model.load(workspaceId: "w1")
    await model.setWindowDays(14)
    await model.setWindowDays(30)
    await model.setWindowDays(30)
    await model.setWindowDays(90)
    #expect(model.windowDays == 30 && stub.requests.count == 3)
    let since = stub.requests.map { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?.queryItems?.first?.value }
    #expect(since == ["2026-09-26", "2026-09-19", "2026-09-03"])
  }

  @Test func aWorkspaceSwitchClearsItemsBeforeTheNewRequestReturns() async {
    let transport = ChangesSwitchTransport(first: .init(status: 200, body: changesBody([change("signal_opened")])))
    let model = model(transport)
    await model.load(workspaceId: "w1")
    let loading = Task { await model.load(workspaceId: "w2") }
    await transport.gated.waitForRequests(1)
    #expect(model.items.isEmpty && model.groupedByDay.isEmpty && model.phase == .loading)
    transport.gated.releaseAll()
    await loading.value
    #expect(model.workspaceId == "w2" && model.items.isEmpty)
    if case .failed = model.phase {} else { Issue.record("Expected failed") }
  }

  @Test func aLateReplyFromAnotherWorkspaceCannotReplaceTheCurrentList() async {
    let transport = ChangesSwitchTransport(first: .init(status: 200, body: changesBody([change("signal_opened")])))
    let model = model(transport)
    let oldLoad = Task { await model.load(workspaceId: "w2") }
    await transport.gated.waitForRequests(1)
    await model.load(workspaceId: "w1")
    transport.gated.releaseAll()
    await oldLoad.value
    #expect(model.workspaceId == "w1" && model.phase == .ready && model.items.count == 1)
  }

  @Test func aFailureSetsFailedEvenWhenThePreviousListExists() async {
    let stub = StubTransport([.init(status: 200, body: changesBody([change("signal_opened")])), .init(status: 500, body: "{}")])
    let model = model(stub)
    await model.load(workspaceId: "w1")
    await model.load(workspaceId: "w1")
    if case .failed(let message) = model.phase { #expect(!message.isEmpty) }
    else { Issue.record("Expected failed") }
  }
}

private final class ChangesSwitchTransport: HTTPTransport, Sendable {
  let first: StubTransport
  let gated = GatedTransport(reply: .init(status: 500, body: "{}"))
  init(first: StubTransport.Reply) { self.first = StubTransport([first]) }
  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    if request.url!.path.contains("/w1/") { return try await first.send(request) }
    return try await gated.send(request)
  }
}
