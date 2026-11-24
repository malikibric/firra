import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct OnboardingTests {
  let running = #"{"state":"running","items":[]}"#
  let ready = #"{"state":"ready","items":[{"domain":"acme.example","thread_count":12,"sample_names":["Synthetic Company"]}]}"#
  let ai = #"{"tools":[{"tool":"claude_code","installed":true,"signed_in":true}],"queue":{"pending":37},"state":"conversation_only"}"#
  func preferences() -> UserDefaults { UserDefaults(suiteName: "onboarding-test-\(UUID())")! }
  func api(_ stub: StubTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: stub, tokenProvider: { "synthetic-local-token" })
  }

  @Test func allFiveStepsUseTheLocalEngineAndResumeWithoutOAuth() async throws {
    let attention = try String(contentsOf: #require(Bundle.module.url(forResource: "attention",
      withExtension: "json", subdirectory: "Fixtures/LocalRoutes")), encoding: .utf8)
    let stub = StubTransport([
      .init(status: 200, body: #"{"gmail_connection_id":"gmail","calendar_connection_id":"calendar"}"#),
      .init(status: 202, body: #"{"state":"running"}"#), .init(status: 200, body: running),
      .init(status: 200, body: ready), .init(status: 201, body: #"{"id":"client"}"#),
      .init(status: 200, body: ai),
      .init(status: 200, body: #"{"executor":"claude_code","backfill_months":6,"background_acknowledged":true}"#),
      .init(status: 200, body: attention)])
    let defaults = preferences()
    var slots: [(String, String)] = []
    let model = OnboardingModel(api: api(stub), workspace: "workspace", defaults: defaults,
      authorize: { GoogleSignInResult(refreshToken: "synthetic-refresh", email: "owner@example.test") },
      putSecret: { slots.append(($0, $1)) })
    model.advance(); #expect(model.step == .welcome)
    await model.welcome.check { .off }
    #expect(model.welcome.warning?.contains("System Settings") == true)
    model.welcome.acknowledged = true; model.advance()
    model.google.backfillMonths = 6
    await model.google.connect(); model.advance()
    #expect(model.step == .clients && model.google.connectionId == "gmail")
    let googleBody = try #require(JSONSerialization.jsonObject(with: stub.requests[0].httpBody!) as? [String: Any])
    #expect(Set(googleBody.keys) == ["connection_id", "account_label", "backfill_months"])
    #expect(slots.count == 1 && slots[0].0 == "synthetic-refresh")
    #expect(slots[0].1 == "google_refresh_token:" + (googleBody["connection_id"] as! String))
    #expect(googleBody["backfill_months"] as? Int == 6)
    await model.clients.poll(connectionId: "gmail", start: true)
    #expect(model.clients.state == "running")
    await model.clients.poll(connectionId: "gmail")
    #expect(model.clients.choices[0].name == "Acme")
    #expect(model.clients.choices[0].sampleNames == ["Synthetic Company"])
    #expect(!model.clients.choices[0].selected && !model.clients.canSave)
    model.clients.choices[0].selected = true
    model.clients.choices[0].policy = .excluded
    await model.clients.saveClients(); model.advance()
    #expect(model.step == .clients && stub.requests.count == 4)
    model.clients.choices[0].confirmedExclusion = true
    await model.clients.saveClients()
    #expect(model.clients.message == "Reading mail for your clients.")
    let clientBody = try #require(JSONSerialization.jsonObject(with: stub.requests[4].httpBody!) as? [String: Any])
    #expect(clientBody["confirm_exclusion"] as? Bool == true)
    #expect(clientBody["policy"] as? String == "excluded")
    model.advance(); await model.ai.load()
    #expect(model.ai.waiting == "37 emails waiting" && model.ai.status?.tools[0].signedIn == true)
    model.ai.background = true
    await model.ai.save(backfillMonths: 6); model.advance()
    #expect(model.step == .ai && stub.requests.count == 6)
    model.ai.acknowledged = true
    await model.ai.save(backfillMonths: 6); model.advance()
    #expect(model.step == .results)
    await model.results.poll(); model.finish()
    #expect(model.step == .done)
    let resumed = OnboardingModel(api: api(stub), workspace: "workspace", defaults: defaults,
      authorize: { Issue.record("Resume must not authorize"); throw GoogleSignInError.cancelled },
      putSecret: { _, _ in Issue.record("Resume must not write a secret") })
    #expect(resumed.step == .done && stub.requests.count == 8)
    model.runAgain()
    #expect(model.step == .welcome && !model.clients.saved && !model.ai.saved)
    #expect(!model.welcome.acknowledged && model.clients.choices.isEmpty)
  }

  @Test(arguments: [GoogleSignInError.cancelled, .noRefreshToken, .denied("synthetic"),
    .timeout, .missingScopes(["synthetic"]), .stateMismatch, .exchangeFailed(400), .transport])
  func googleFailuresDoNotSendCredentialsOrAdvance(_ error: GoogleSignInError) async {
    let stub = StubTransport([])
    let model = GoogleSetupModel(api: api(stub), defaults: preferences(), authorize: { throw error },
      putSecret: { _, _ in Issue.record("No grant should be stored") })
    await model.connect()
    #expect(model.connectionId == nil && model.message != nil && !model.busy)
    #expect(stub.requests.isEmpty)
    if case .denied = error { #expect(model.message?.contains("Google test user") == true) }
    #expect(GoogleSetupModel.text(GoogleOAuthConfigError.missing).contains("Developer setup"))
  }

  @Test func secretAndConnectionFailuresLeaveSetupRetryable() async {
    let stub = StubTransport([.init(status: 500, body: "{}")])
    let defaults = preferences()
    var failSecret = true
    let model = GoogleSetupModel(api: api(stub), defaults: defaults,
      authorize: { GoogleSignInResult(refreshToken: "synthetic", email: "owner@example.test") },
      putSecret: { _, _ in if failSecret { throw GoogleSignInError.transport } })
    await model.connect()
    #expect(stub.requests.isEmpty && model.connectionId == nil)
    failSecret = false; await model.connect()
    #expect(stub.requests.count == 1 && model.connectionId == nil && model.message != nil)
    #expect(defaults.string(forKey: "setup.google.connection") == nil)
  }

  @Test func failedSuggestionsAndWritesCanBeRetried() async {
    let stub = StubTransport([.init(status: 500, body: "{}"),
      .init(status: 202, body: #"{"state":"running"}"#),
      .init(status: 200, body: #"{"state":"failed","items":[]}"#),
      .init(status: 200, body: ready), .init(status: 500, body: "{}"),
      .init(status: 201, body: #"{"id":"client"}"#)])
    let model = ClientsSetupModel(api: api(stub), workspace: "workspace", defaults: preferences())
    await model.poll(connectionId: "gmail")
    #expect(model.state == "failed" && model.message != nil)
    model.reset(); await model.poll(connectionId: "gmail", start: true)
    #expect(model.state == "failed" && model.message != nil)
    await model.poll(connectionId: "gmail")
    model.choices[0].selected = true
    model.choices[0].policy = .metadataOnly
    await model.saveClients(); #expect(!model.saved && model.message != nil)
    await model.saveClients(); #expect(model.saved)
    #expect(stub.requests.last?.url?.path == "/v1/workspaces/workspace/clients")
  }

  @Test(arguments: [("acme.com", "acme.com"), ("  Acme.COM ", "acme.com"), ("@acme.com", "acme.com"),
    ("https://www.acme.com/team", "acme.com"), ("ceo@mail.acme.co.uk", "mail.acme.co.uk")])
  func domainsAreNormalized(input: String, expected: String) {
    #expect(ClientsSetupModel.normalizedDomain(input) == expected)
  }

  @Test(arguments: ["", "acme", "a b.com", "acme..com", "-acme.com", "acme.com-", "ac_me.com", "@"])
  func notDomainsAreRefused(input: String) {
    #expect(ClientsSetupModel.normalizedDomain(input) == nil)
  }

  @Test func clientsAreTypedByTheOwnerWithoutReadingMail() async {
    let stub = StubTransport([.init(status: 201, body: #"{"id":"client"}"#),
      .init(status: 201, body: #"{"id":"client2"}"#)])
    let model = ClientsSetupModel(api: api(stub), workspace: "workspace", defaults: preferences())
    #expect(!model.canSave && model.state == "none")
    model.newDomain = "nonsense"; model.addClient()
    #expect(model.choices.isEmpty && model.message != nil)
    model.newName = "Acme Corp"; model.newDomain = "https://www.acme.com"; model.addClient()
    #expect(model.choices.count == 1 && model.choices[0].name == "Acme Corp" && model.choices[0].domain == "acme.com")
    #expect(model.choices[0].selected && model.canSave && model.newName.isEmpty && model.newDomain.isEmpty)
    model.newDomain = "ACME.com"; model.addClient()
    #expect(model.choices.count == 1 && model.message?.contains("already") == true)
    model.newDomain = "globex.io"; model.addClient()
    #expect(model.choices[1].name == "Globex")
    await model.saveClients()
    #expect(model.saved && stub.requests.count == 2)
    #expect(stub.requests.allSatisfy { $0.url?.path == "/v1/workspaces/workspace/clients" })
    #expect(!stub.requests.contains { $0.url?.path.contains("onboarding") == true })
  }

  @Test func suggestionsAreOptionalAndAddedUntickedBesideTypedClients() async {
    let stub = StubTransport([.init(status: 202, body: #"{"state":"running"}"#),
      .init(status: 200, body: running), .init(status: 200, body: ready)])
    let model = ClientsSetupModel(api: api(stub), workspace: "workspace", defaults: preferences())
    model.newDomain = "acme.com"; model.addClient()
    await model.poll(connectionId: "gmail", start: true)
    await model.poll(connectionId: "gmail")
    #expect(model.state == "ready" && model.choices.count == 2)
    #expect(model.choices[0].domain == "acme.com" && model.choices[0].selected)
    #expect(model.choices[1].domain == "acme.example" && !model.choices[1].selected)
  }

  @Test func aiErrorsRemainRetryableAndConversationNeedsNoAcknowledgement() async {
    let stub = StubTransport([.init(status: 500, body: "{}"), .init(status: 500, body: "{}"),
      .init(status: 200, body: #"{"executor":"conversation_only","backfill_months":3,"background_acknowledged":false}"#)])
    let model = AISetupModel(api: api(stub))
    await model.load(); #expect(model.message != nil)
    await model.save(backfillMonths: 3); #expect(!model.saved && model.message != nil)
    await model.save(backfillMonths: 3); #expect(model.saved)
    let body = try? JSONSerialization.jsonObject(with: stub.requests.last!.httpBody!) as? [String: Any]
    #expect(body?["executor"] as? String == "conversation_only")
    #expect(body?["background_acknowledged"] as? Bool == false)
  }

  @Test func resultsExplainWaitingAndStopAtSixtySecondsEvenAfterErrors() async {
    let stub = StubTransport([.init(status: 200, body: #"{"generated_at":"2026-10-05T12:00:00Z","items":[]}"#),
      .init(status: 200, body: #"{"engine_version":"test","database":"ready","connections":[]}"#),
      .init(status: 200, body: ai), .init(status: 500, body: "{}")])
    var now = Date()
    let model = FirstResultsModel(api: api(stub), workspace: "workspace", clock: { now })
    model.begin(); await model.poll()
    #expect(model.message.contains("in conversation") && !model.finished)
    await model.poll(); #expect(model.message.contains("local engine"))
    now = now.addingTimeInterval(60); await model.poll()
    #expect(model.finished && stub.requests.count == 4)
  }

  @Test(arguments: ["syncing", "needs_signin", "tool_missing", "waiting_for_limit", "working"])
  func firstResultsExplainEachWait(_ state: String) async {
    let expected = ["syncing": "reading mail", "needs_signin": "Sign in", "tool_missing": "install",
      "waiting_for_limit": "limit", "working": "37 emails"]
    let local = state == "syncing"
      ? #"{"engine_version":"test","database":"ready","connections":[{"id":"gmail","provider":"gmail","account_label":"synthetic","state":"syncing"}]}"#
      : #"{"engine_version":"test","database":"ready","connections":[]}"#
    let stub = StubTransport([.init(status: 200, body: #"{"generated_at":"2026-10-05T12:00:00Z","items":[]}"#),
      .init(status: 200, body: local),
      .init(status: 200, body: ai.replacingOccurrences(of: "conversation_only", with: state))])
    let model = FirstResultsModel(api: api(stub), workspace: "workspace")
    await model.poll()
    #expect(model.message.contains(expected[state]!) && !model.finished)
  }

  @Test func resumedClientSaveDoesNotDuplicateOrReactivateClients() async throws {
    let defaults = preferences()
    defaults.set("saved-client", forKey: "setup.created.workspace.acme.example")
    let stub = StubTransport([.init(status: 200, body: ready),
      .init(status: 204, body: ""), .init(status: 204, body: "")])
    let model = ClientsSetupModel(api: api(stub), workspace: "workspace", defaults: defaults)
    await model.poll(connectionId: "gmail")
    model.choices[0].selected = true
    await model.saveClients()
    #expect(model.saved && stub.requests.count == 3)
    #expect(stub.requests[1].httpMethod == "PATCH")
    let body = try #require(JSONSerialization.jsonObject(with: stub.requests[1].httpBody!) as? [String: Any])
    #expect(Set(body.keys) == ["name"])
    #expect(stub.requests[2].httpMethod == "PUT")
  }

  @Test func unfinishedSetupResumesItsStepAndMissingConnectionReturnsToGoogle() {
    let defaults = preferences()
    defaults.set(OnboardingModel.Step.ai.rawValue, forKey: "setup.step")
    defaults.set("gmail", forKey: "setup.google.connection")
    func make() -> OnboardingModel {
      OnboardingModel(api: api(StubTransport([])), workspace: "workspace", defaults: defaults,
        authorize: { throw GoogleSignInError.cancelled }, putSecret: { _, _ in })
    }
    #expect(make().step == .ai)
    defaults.removeObject(forKey: "setup.google.connection")
    #expect(make().step == .google)
  }

  @Test func fileVaultParsingAndWarningsDoNotRequireARealSupervisor() async {
    #expect(FileVaultState.parse("FileVault is On.\n") == .on)
    #expect(FileVaultState.parse("FileVault is Off.\n") == .off)
    #expect(FileVaultState.parse("unexpected") == .unknown)
    let model = WelcomeSetupModel()
    await model.check { .on }; #expect(model.warning == nil)
    await model.check { .off }; #expect(model.warning != nil)
  }
}

@Suite struct SecretSlotCaseTests {
  @Test func googleSlotUsesTheLowercaseIdTheEngineLooksUp() {
    #expect(SecretSlot.googleRefreshToken("ABCDEF01-2345-6789-ABCD-EF0123456789")
      == "google_refresh_token:abcdef01-2345-6789-abcd-ef0123456789")
  }
}
