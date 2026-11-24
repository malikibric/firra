import CoreFoundation
import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct SourceAccessTests {
  func api(_ transport: any HTTPTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: transport, tokenProvider: { "synthetic" })
  }
  func fixture() throws -> String {
    try String(contentsOf: #require(Bundle.module.url(forResource: "mcp-settings", withExtension: "json",
      subdirectory: "Fixtures/LocalRoutes")), encoding: .utf8)
  }

  @Test func defaultIsOffAndCapturedRouteLoadsWithoutWriting() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture())])
    let model = SourceAccessModel(api: api(stub))
    #expect(!model.enabled && !model.loaded && !model.confirmationRequired)
    await model.requestChange(to: true); await model.confirm()
    #expect(stub.requests.isEmpty && !model.enabled)
    await model.load()
    #expect(model.loaded && !model.enabled && !model.loading && model.message == nil)
    #expect(stub.requests.count == 1 && stub.requests[0].httpMethod == "GET")
    #expect(stub.requests[0].url?.path == "/local/mcp/settings")
  }

  @Test func loadFailureStaysOffAndRetryReadsStoredValue() async {
    let stub = StubTransport([.init(status: 500, body: #"{"message":"private details"}"#),
      .init(status: 200, body: #"{"source_access":true}"#)])
    let model = SourceAccessModel(api: api(stub))
    await model.load()
    #expect(!model.enabled && !model.loaded && !model.loading)
    #expect(model.message == "Source access could not be loaded. Try again.")
    await model.requestChange(to: true); #expect(stub.requests.count == 1)
    await model.load()
    #expect(model.enabled && model.loaded && model.message == nil && !model.confirmationRequired)
    #expect(stub.requests.allSatisfy { $0.httpMethod == "GET" })
  }

  @Test func enablingRequiresConfirmationAndCancellationNeverWrites() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture()),
      .init(status: 200, body: #"{"source_access":true}"#)])
    let model = SourceAccessModel(api: api(stub))
    await model.load(); await model.requestChange(to: true)
    #expect(model.confirmationRequired && !model.enabled && stub.requests.count == 1)
    await model.load(); #expect(stub.requests.count == 1)
    model.cancelConfirmation(); await model.confirm()
    #expect(!model.confirmationRequired && !model.enabled && stub.requests.count == 1)
    await model.requestChange(to: true); await model.confirm(); await model.confirm()
    #expect(model.enabled && !model.confirmationRequired && stub.requests.count == 2)
    let request = stub.requests[1]
    #expect(request.httpMethod == "PUT" && request.url?.path == "/local/mcp/settings")
    let data = try #require(request.httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    #expect(Set(body.keys) == ["source_access"])
    let value = try #require(body["source_access"] as? NSNumber)
    #expect(CFGetTypeID(value) == CFBooleanGetTypeID() && value.boolValue)
  }

  @Test func disablingNeedsNoConfirmationAndWritesFalse() async throws {
    let stub = StubTransport([.init(status: 200, body: #"{"source_access":true}"#),
      .init(status: 200, body: #"{"source_access":false}"#)])
    let model = SourceAccessModel(api: api(stub))
    await model.load(); await model.requestChange(to: false)
    #expect(!model.enabled && !model.confirmationRequired && model.message == nil)
    let data = try #require(stub.requests[1].httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    let value = try #require(body["source_access"] as? NSNumber)
    #expect(CFGetTypeID(value) == CFBooleanGetTypeID() && !value.boolValue)
    await model.requestChange(to: false); #expect(stub.requests.count == 2)
  }

  @Test(arguments: [false, true]) func failedSaveRevertsTheSwitch(previous: Bool) async {
    let stub = StubTransport([.init(status: 200, body: "{\"source_access\":\(previous)}"),
      .init(status: 500, body: #"{"message":"private details"}"#)])
    let model = SourceAccessModel(api: api(stub))
    await model.load(); await model.requestChange(to: !previous)
    if !previous { await model.confirm() }
    #expect(model.enabled == previous && !model.saving && !model.confirmationRequired)
    #expect(model.message == "Source access could not be saved. Try again.")
    #expect(stub.requests.count == 2)
  }

  @Test func malformedSettingsNeverEnableSourceAccess() async {
    let stub = StubTransport([.init(status: 200, body: #"{"source_access":"true"}"#)])
    let model = SourceAccessModel(api: api(stub))
    await model.load()
    #expect(!model.loaded && !model.enabled && model.message != nil)
  }

  @Test func loadingAndSavingAreVisibleAndConcurrentRequestsAreIgnored() async {
    // A gated response exposes both loading and saving without touching the network.
    let loadGate = GatedTransport(reply: .init(status: 200, body: #"{"source_access":true}"#))
    let model = SourceAccessModel(api: api(loadGate))
    let load = Task { await model.load() }
    await loadGate.waitForRequests(1)
    #expect(model.loading && !model.loaded)
    await model.load(); await model.requestChange(to: true)
    #expect(loadGate.requestCount == 1)
    loadGate.releaseAll(); await load.value
    let save = Task { await model.requestChange(to: false) }
    await loadGate.waitForRequests(2)
    #expect(model.saving && !model.enabled)
    await model.load(); await model.requestChange(to: true)
    #expect(loadGate.requestCount == 2 && !model.confirmationRequired)
    loadGate.releaseAll(); await save.value
    // The response, rather than an optimistic value, is authoritative.
    #expect(!model.saving && model.enabled)
  }

  @Test func bothScreensShareValueAndRunSetupAgainDoesNotResetIt() async throws {
    let stub = StubTransport([.init(status: 200, body: #"{"source_access":true}"#),
      .init(status: 200, body: #"{"source_access":false}"#),
      .init(status: 200, body: #"{"source_access":false}"#)])
    let shared = SourceAccessModel(api: api(stub))
    let suite = "source-access-test-\(UUID().uuidString)"
    let defaults = try #require(UserDefaults(suiteName: suite))
    defer { defaults.removePersistentDomain(forName: suite) }
    let onboarding = OnboardingModel(api: api(stub), workspace: "synthetic", defaults: defaults,
      sourceAccess: shared, authorize: { throw GoogleSignInError.cancelled }, putSecret: { _, _ in })
    let tools = ConnectAIToolsModel(api: api(stub), sourceAccess: shared, copy: { _ in true }, open: { _ in true })
    #expect(onboarding.sourceAccess === tools.sourceAccess)
    await onboarding.sourceAccess.load()
    #expect(tools.sourceAccess.enabled)
    onboarding.runAgain()
    #expect(onboarding.step == .welcome && tools.sourceAccess.enabled && stub.requests.count == 1)
    await tools.sourceAccess.requestChange(to: false)
    #expect(!onboarding.sourceAccess.enabled)
    await onboarding.sourceAccess.load() // re-enter onboarding: reads, never resets the setting
    #expect(!tools.sourceAccess.enabled && stub.requests[2].httpMethod == "GET")
  }
}
