import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct MenuStatusTests {
  func status(database: String = "ready", state: String = "idle", error: String? = nil,
              aiState: String = "conversation_only", executor: String = "conversation_only") -> String {
    let errorJSON = error.map { ",\"error_class\":\"\($0)\"" } ?? ""
    return """
    {"engine_version":"test","database":"\(database)","connections":[
      {"id":"gmail","provider":"gmail","account_label":"synthetic","state":"\(state)",
       "last_synced_at":"2026-10-05T10:00:00Z"\(errorJSON)},
      {"id":"slack","provider":"slack","account_label":"synthetic","state":"needs_reauth"}],
      "ai":{"executor":"\(executor)","tools":[],"queue":{"pending":42},"state":"\(aiState)"}}
    """
  }
  var emptyAttention: String { #"{"generated_at":"2026-10-05T10:00:00Z","items":[]}"# }
  func attention() throws -> String {
    try String(contentsOf: #require(Bundle.module.url(forResource: "attention", withExtension: "json",
      subdirectory: "Fixtures/LocalRoutes")), encoding: .utf8)
  }
  func preferences() -> UserDefaults { UserDefaults(suiteName: "menu-test-\(UUID())")! }
  func api(_ stub: StubTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: stub, tokenProvider: { "synthetic" })
  }
  func replies(_ status: String, attention: String? = nil, cards: String = #"{"items":[],"next_cursor":null}"#) -> [StubTransport.Reply] {
    [.init(status: 200, body: status), .init(status: 200, body: attention ?? emptyAttention),
     .init(status: 200, body: cards)]
  }

  @Test(arguments: ["starting", "migrating", "error"])
  func engineProblemsHavePriorityOverActionsAndAttention(_ database: String) async {
    let stub = StubTransport([.init(status: 200, body: status(database: database,
      state: "needs_reauth", aiState: "needs_signin", executor: "claude_code"))])
    let model = MenuStatusModel(api: api(stub), workspace: "w", owner: "o", defaults: preferences())
    let notices = await model.refresh(active: true)
    #expect(model.title == (database == "error" ? "Firra stopped" : "Firra is starting"))
    #expect(notices.actions.isEmpty && stub.requests.count == 1)
  }

  @Test func attentionBeatsSyncAndActionsBeatAttention() async throws {
    let stub = StubTransport(replies(status(state: "syncing"), attention: try attention()) +
      replies(status(state: "needs_reauth", error: "token_revoked"), attention: try attention()) +
      replies(status(state: "syncing")) + replies(status()))
    let model = MenuStatusModel(api: api(stub), workspace: "w", owner: "o", defaults: preferences())
    _ = await model.refresh(active: true)
    #expect(model.title == "1 client needs attention" && model.icon == "exclamationmark.bubble")
    _ = await model.refresh(active: true)
    #expect(model.title == "Firra needs you" && model.reconnectConnection?.id == "gmail")
    #expect(model.connections.count == 1)
    _ = await model.refresh(active: true); #expect(model.title == "Syncing")
    _ = await model.refresh(active: true); #expect(model.title == "Firra")
    model.health = .stopped; #expect(model.title == "Firra stopped")
    model.health = .starting; #expect(model.title == "Firra is starting")
  }

  @Test(arguments: ["needs_signin", "tool_missing", "not_signed_in", "not_installed"])
  func aiActionStatesAndBackgroundWording(_ state: String) async {
    let stub = StubTransport(replies(status(aiState: state, executor: "claude_code")))
    let model = MenuStatusModel(api: api(stub), workspace: "w", owner: "o", defaults: preferences())
    let notices = await model.refresh(active: true)
    #expect(model.title == "Firra needs you" && model.actions == [.claude])
    #expect(model.waiting == "42 emails waiting for your AI" && notices.actions == [.claude])
  }

  @Test func cadenceUsesFifteenSecondsActiveAndSixtyInactive() async {
    let stub = StubTransport(Array(repeating: replies(status()), count: 4).flatMap { $0 })
    var now = Date()
    let model = MenuStatusModel(api: api(stub), workspace: "w", owner: "o", defaults: preferences(), clock: { now })
    _ = await model.pollIfDue(active: true)
    #expect(stub.requests.count == 3 && model.waiting == "42 emails waiting: ask your AI to process mail")
    now = now.addingTimeInterval(14); _ = await model.pollIfDue(active: true)
    #expect(stub.requests.count == 3)
    now = now.addingTimeInterval(1); _ = await model.pollIfDue(active: true)
    #expect(stub.requests.count == 6)
    now = now.addingTimeInterval(59); _ = await model.pollIfDue(active: false)
    #expect(stub.requests.count == 6)
    now = now.addingTimeInterval(1); _ = await model.pollIfDue(active: false)
    #expect(stub.requests.count == 9)
    now = now.addingTimeInterval(15); _ = await model.pollIfDue(active: true)
    #expect(stub.requests.count == 12)
    #expect(stub.requests.filter { $0.url?.path == "/local/status" }.count == 4)
  }

  @Test func importantIdsAndDailyActionsPersistAcrossRelaunchAndRespectFocus() async throws {
    let defaults = preferences()
    var now = Date(timeIntervalSince1970: 1791187200)
    let helper = ImportanceTests()
    let cards = helper.page([helper.cardJSON("one", importance: "important"),
      helper.cardJSON("two", importance: "fyi"), helper.cardJSON("three", importance: "important")])
    let status = status(state: "needs_reauth", aiState: "needs_signin", executor: "claude_code")
    let stub = StubTransport(Array(repeating: replies(status, cards: cards), count: 5).flatMap { $0 })
    func make() -> MenuStatusModel {
      MenuStatusModel(api: api(stub), workspace: "w", owner: "o", defaults: defaults, clock: { now })
    }
    let model = make()
    model.focus.clientIDs = ["c1"]; model.focus.actionKinds = ["google", "claude"]
    let focused = await model.refresh(active: true)
    #expect(focused.decisions.isEmpty && focused.actions.isEmpty)
    let background = await model.refresh(active: false)
    #expect(background.decisions.map(\.id) == ["one", "three"] && background.actions == [.google, .claude])
    let relaunched = make()
    let duplicate = await relaunched.refresh(active: true)
    #expect(duplicate.decisions.isEmpty && duplicate.actions.isEmpty)
    now = now.addingTimeInterval(86400)
    let tomorrow = await relaunched.refresh(active: true)
    #expect(tomorrow.decisions.isEmpty && tomorrow.actions == [.google, .claude])
    #expect((await relaunched.refresh(active: true)).actions.isEmpty)
  }

  @Test func focusedDecisionOnlySuppressesThatCardAndOldIdsNeverExpire() throws {
    let defaults = preferences()
    let store = UserDefaultsShownCardStore(userID: "owner", defaults: defaults)
    store.ids = (0..<300).map { "d\($0)" }
    let planner = NotificationPlanner(store: store)
    let helper = ImportanceTests()
    let cards = try [helper.card("d0"), helper.card("fresh"), helper.card("other")]
    #expect(planner.plan(cards: cards, focusedDecisionIDs: ["fresh"]).map(\.id) == ["other"])
    #expect(planner.plan(cards: cards).map(\.id) == ["fresh"])
    #expect(NotificationPlanner(store: UserDefaultsShownCardStore(userID: "owner", defaults: defaults))
      .plan(cards: cards).isEmpty)
  }

  @Test func syncTimeAcceptsBothEngineTimestampFormats() {
    #expect(MenuStatusModel.syncDate(nil) == nil)
    #expect(MenuStatusModel.syncDate("2026-10-05T10:00:00.123456+00:00") != nil)
    #expect(MenuStatusModel.syncDate("2026-10-05T10:00:00Z") != nil)
  }

  @Test func statusAndNotificationErrorsDoNotInventDataAndSyncUsesLocalPost() async {
    let stub = StubTransport([.init(status: 500, body: "{}"),
      .init(status: 200, body: status()), .init(status: 500, body: "{}"), .init(status: 500, body: "{}"),
      .init(status: 202, body: #"{"status":"queued"}"#), .init(status: 409, body: "{}")])
    let model = MenuStatusModel(api: api(stub), workspace: "w", owner: "o", defaults: preferences())
    #expect((await model.refresh(active: true)).decisions.isEmpty && model.health == .stopped)
    #expect((await model.refresh(active: true)).decisions.isEmpty && model.attention.isEmpty)
    await model.syncNow(); #expect(model.message == "Sync requested.")
    let request = stub.requests[4]
    #expect(request.httpMethod == "POST" && request.url?.path == "/local/connections/gmail/sync")
    await model.syncNow(); #expect(model.message?.contains("could not be started") == true)
  }
}
