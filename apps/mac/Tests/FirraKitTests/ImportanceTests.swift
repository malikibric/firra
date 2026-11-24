import Foundation
import Testing
@testable import FirraKit

@MainActor
private final class MemoryShownCards: ShownCardStore {
  var ids: [String] = []
}

@MainActor @Suite struct ImportanceTests {
  func cardJSON(_ id: String = "d1", importance: String? = nil, day: String? = nil,
                status: String = "new", evidence: [String] = []) -> String {
    let ids = evidence.map { "\"\($0)\"" }.joined(separator: ",")
    let extra = importance.map { ",\"importance\":\"\($0)\"" } ?? ""
    let date = day.map { ",\"surfaced_on\":\"\($0)\"" } ?? ""
    return """
    {"id":"\(id)","client_id":"c1","client_name":"Acme","week":"2026-10-05",
    "kind":"commitment_watch","title":"Call the client","body":"Private card details",
    "play":"protect","strength":"strong","status":"\(status)","evidence_ids":[\(ids)],
    "feedback":null\(extra)\(date)}
    """
  }

  func card(_ id: String = "d1", importance: String? = "important", day: String? = nil,
            status: String = "new") throws -> DecisionItem {
    try JSONDecoder.api.decode(DecisionItem.self, from: Data(cardJSON(id, importance: importance, day: day, status: status).utf8))
  }

  func page(_ cards: [String]) -> String { "{\"items\":[\(cards.joined(separator: ","))],\"next_cursor\":null}" }

  func api(_ stub: StubTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: stub, tokenProvider: { "tok" })
  }

  @Test func oldNullAndRoutineFieldsDecode() throws {
    let old = try card(importance: nil)
    #expect(old.importance == nil && old.surfacedOn == nil)
    let null = cardJSON().dropLast() + ",\"importance\":null,\"surfaced_on\":null}"
    let decoded = try JSONDecoder.api.decode(DecisionItem.self, from: Data(null.utf8))
    #expect(decoded.importance == nil && decoded.surfacedOn == nil)
    let routine = try card(day: "2026-10-05")
    #expect(routine.importance == "important" && routine.surfacedOn == "2026-10-05")
    for kind in ["state_change", "commitment_watch", "expansion", "risk"] {
      let raw = cardJSON().replacingOccurrences(of: "commitment_watch", with: kind)
      #expect(try JSONDecoder.api.decode(DecisionItem.self, from: Data(raw.utf8)).kind == kind)
    }
  }

  @Test func filtersAreOnlySentWhenSet() async throws {
    let stub = StubTransport([.init(status: 200, body: page([])), .init(status: 200, body: page([]))])
    let client = api(stub)
    _ = try await client.decisions(workspaceId: "w")
    _ = try await client.decisions(workspaceId: "w", status: "all", importance: "important", kind: "commitment_watch", cursor: "next")
    let old = URLComponents(url: stub.requests[0].url!, resolvingAgainstBaseURL: false)!.queryItems!
    #expect(Set(old.map(\.name)) == ["status", "limit"])
    let new = URLComponents(url: stub.requests[1].url!, resolvingAgainstBaseURL: false)!.queryItems!
    #expect(Dictionary(uniqueKeysWithValues: new.map { ($0.name, $0.value!) }) ==
      ["status": "all", "limit": "50", "importance": "important", "kind": "commitment_watch", "cursor": "next"])
  }

  @Test func metadataShownWithoutFetchingAndEvidenceWaitsForAQuote() async throws {
    let raw = page([cardJSON("metadata"), cardJSON("quoted", evidence: ["e1"])])
    let stub = StubTransport([.init(status: 200, body: raw), .init(status: 404, body: "{}"),
      .init(status: 200, body: #"{"id":"e1","quote":"A verified synthetic quote","occurred_at":"2026-10-05"}"#)])
    let model = DecisionsModel(api: api(stub))
    let metadata = try JSONDecoder.api.decode(DecisionItem.self, from: Data(cardJSON().utf8))
    #expect(model.isVerified(metadata))
    await model.load(workspaceId: "w")
    #expect(model.isVerified(model.cards[0]) && !model.isVerified(model.cards[1]))
    #expect(stub.requests.count == 2 && model.quotesFailed == ["quoted"])
    await model.loadEvidence(workspaceId: "w", card: model.cards[0])
    #expect(stub.requests.count == 2)
    await model.loadEvidence(workspaceId: "w", card: model.cards[1])
    #expect(model.isVerified(model.cards[1]) && stub.requests.count == 3)
  }

  @Test func sectionsPreserveServerOrderAndOmitEmptyGroups() async {
    let raw = page([cardJSON("f", importance: "fyi"), cardJSON("i2", importance: "important"),
      cardJSON("weekly"), cardJSON("a", importance: "attention"), cardJSON("i1", importance: "important")])
    let model = DecisionsModel(api: api(StubTransport([.init(status: 200, body: raw),
      .init(status: 200, body: page([cardJSON("only", importance: "attention")]))])))
    await model.load(workspaceId: "w")
    #expect(model.sections.map(\.id) == ["Important", "Worth attention", "FYI", "This week"])
    #expect(model.sections.map { $0.cards.map(\.id) } == [["i2", "i1"], ["a"], ["f"], ["weekly"]])
    await model.load(workspaceId: "w")
    #expect(model.sections.map(\.id) == ["Worth attention"])
  }

  @Test func plannerPreservesEngineOrderAndRemembersAcrossInstances() throws {
    let store = MemoryShownCards()
    let planner = NotificationPlanner(store: store)
    let cards = try [card("old", day: "2026-10-01"), card("b", day: "2026-10-05"),
      card("a", day: "2026-10-05"), card("mid", day: "2026-10-04"), card("missing"),
      card("attention", importance: "attention"), card("fyi", importance: "fyi"),
      card("weekly", importance: nil), card("done", status: "done"), card("dismissed", status: "dismissed")]
    #expect(planner.plan(cards: cards + [cards[1]]).map(\.id) == ["old", "b", "a", "mid", "missing"])
    #expect(store.ids == ["old", "b", "a", "mid", "missing"])
    let restarted = NotificationPlanner(store: store)
    #expect(restarted.plan(cards: cards).isEmpty)
    #expect(restarted.plan(cards: cards).isEmpty)
  }

  @Test func memoryKeepsEveryIdAndDefaultsPersistPerUser() throws {
    let store = MemoryShownCards()
    store.ids = (0..<200).map { "shown-\($0)" }
    #expect(NotificationPlanner(store: store).plan(cards: [try card("new")]).count == 1)
    #expect(store.ids.count == 201 && store.ids.first == "shown-0" && store.ids.last == "new")
    let suite = "firra-importance-test-\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suite)!
    defer { defaults.removePersistentDomain(forName: suite) }
    let first = UserDefaultsShownCardStore(userID: "one", defaults: defaults)
    _ = NotificationPlanner(store: first).plan(cards: [try card("saved")])
    let restarted = UserDefaultsShownCardStore(userID: "one", defaults: defaults)
    #expect(NotificationPlanner(store: restarted).plan(cards: [try card("saved")]).isEmpty)
    let second = UserDefaultsShownCardStore(userID: "two", defaults: defaults)
    #expect(NotificationPlanner(store: second).plan(cards: [try card("saved")]).count == 1)
    first.ids = (0..<205).map { "\($0)" }
    #expect(first.ids.count == 205 && first.ids.first == "0" && first.ids.last == "204")
  }

  @Test func localNotificationsReadOnlyImportantDecisionsAndRetryFailures() async throws {
    let stub = StubTransport([.init(status: 503, body: "{}"),
      .init(status: 200, body: page([cardJSON("important", importance: "important")]))])
    let client = api(stub)
    await #expect(throws: APIError.self) { _ = try await NotificationRoute.load(api: client, workspaceId: "w") }
    #expect(try await NotificationRoute.load(api: client, workspaceId: "w") == .decisions([try card("important")]))
    #expect(stub.requests.count == 2)
    #expect(stub.requests.allSatisfy { $0.url?.query?.contains("importance=important") == true })
  }
}
