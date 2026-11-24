import CoreFoundation
import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct ProcessingToolsTests {
  func api(_ transport: any HTTPTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: transport, tokenProvider: { "synthetic" })
  }
  func fixture(_ name: String = "mcp-settings") throws -> String {
    try String(contentsOf: #require(Bundle.module.url(forResource: name, withExtension: "json",
      subdirectory: "Fixtures/LocalRoutes")), encoding: .utf8)
  }
  func model(_ transport: any HTTPTransport) -> SourceAccessModel {
    SourceAccessModel(api: api(transport), setting: .processingTools)
  }
  func expectBody(_ request: URLRequest, value: Bool) throws {
    #expect(request.httpMethod == "PUT" && request.url?.path == "/local/mcp/settings")
    let data = try #require(request.httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    #expect(Set(body.keys) == ["processing_tools"])
    let boolean = try #require(body["processing_tools"] as? NSNumber)
    #expect(CFGetTypeID(boolean) == CFBooleanGetTypeID() && boolean.boolValue == value)
  }

  @Test func defaultIsOffAndCapturedRouteLoadsWithoutWriting() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture())])
    let model = model(stub)
    #expect(!model.enabled && !model.loaded && !model.confirmationRequired)
    await model.requestChange(to: true); await model.confirm()
    #expect(stub.requests.isEmpty)
    await model.load()
    #expect(model.loaded && !model.enabled && !model.loading && model.message == nil)
    #expect(stub.requests.count == 1 && stub.requests[0].httpMethod == "GET")
    #expect(stub.requests[0].url?.path == "/local/mcp/settings")
  }

  @Test func loadFailureStaysOffAndRetryReadsStoredValue() async throws {
    let stub = StubTransport([.init(status: 500, body: #"{"message":"private details"}"#),
      .init(status: 200, body: try fixture("mcp-settings-processing-enabled"))])
    let model = model(stub)
    await model.load()
    #expect(!model.enabled && !model.loaded && !model.loading)
    #expect(model.message == "Processing tools could not be loaded. Try again.")
    await model.requestChange(to: true); #expect(stub.requests.count == 1)
    await model.load()
    #expect(model.enabled && model.loaded && model.message == nil && !model.confirmationRequired)
    #expect(stub.requests.allSatisfy { $0.httpMethod == "GET" })
  }

  @Test func enablingRequiresConfirmationAndCancellationNeverWrites() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture()),
      .init(status: 200, body: try fixture("mcp-settings-processing-enabled"))])
    let model = model(stub)
    await model.load(); await model.requestChange(to: true)
    #expect(model.confirmationRequired && !model.enabled && stub.requests.count == 1)
    await model.load(); #expect(stub.requests.count == 1)
    model.cancelConfirmation(); await model.confirm()
    #expect(!model.confirmationRequired && !model.enabled && stub.requests.count == 1)
    await model.requestChange(to: true); await model.confirm(); await model.confirm()
    #expect(model.enabled && !model.confirmationRequired && stub.requests.count == 2)
    try expectBody(stub.requests[1], value: true)
  }

  @Test func disablingNeedsNoConfirmationAndWritesOnlyFalse() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture("mcp-settings-processing-enabled")),
      .init(status: 200, body: try fixture())])
    let model = model(stub)
    await model.load(); await model.requestChange(to: false)
    #expect(!model.enabled && !model.confirmationRequired && model.message == nil)
    try expectBody(stub.requests[1], value: false)
    await model.requestChange(to: false); #expect(stub.requests.count == 2)
  }

  @Test(arguments: [false, true]) func failedSaveRevertsTheSwitch(previous: Bool) async throws {
    let stub = StubTransport([.init(status: 200, body: "{\"source_access\":true,\"processing_tools\":\(previous)}"),
      .init(status: 500, body: #"{"message":"private details"}"#)])
    let model = model(stub)
    await model.load(); await model.requestChange(to: !previous)
    if !previous { await model.confirm() }
    #expect(model.enabled == previous && !model.saving && !model.confirmationRequired)
    #expect(model.message == "Processing tools could not be saved. Try again.")
    #expect(stub.requests.count == 2)
    try expectBody(stub.requests[1], value: !previous)
  }

  @Test func missingProcessingFlagDefaultsOffWithoutLosingSourceAccess() async throws {
    let settings = try JSONDecoder.api.decode(McpSettings.self, from: Data(#"{"source_access":true}"#.utf8))
    #expect(settings.sourceAccess && !settings.processingTools)
    let stub = StubTransport([.init(status: 200, body: #"{"source_access":true}"#)])
    let model = model(stub)
    await model.load()
    #expect(model.loaded && !model.enabled && model.message == nil)
  }

  @Test(arguments: ["\"true\"", "1", "null"])
  func malformedProcessingFlagNeverEnablesTools(value: String) async {
    let stub = StubTransport([.init(status: 200, body: "{\"source_access\":false,\"processing_tools\":\(value)}")])
    let model = model(stub)
    await model.load()
    #expect(!model.loaded && !model.enabled && model.message != nil)
  }

  @Test func loadingAndSavingAreVisibleAndConcurrentRequestsAreIgnored() async throws {
    let gate = GatedTransport(reply: .init(status: 200, body: try fixture()))
    let model = model(gate)
    let load = Task { await model.load() }
    await gate.waitForRequests(1)
    #expect(model.loading && !model.loaded)
    await model.load(); await model.requestChange(to: true)
    #expect(gate.requestCount == 1)
    gate.releaseAll(); await load.value
    await model.requestChange(to: true)
    let save = Task { await model.confirm() }
    await gate.waitForRequests(2)
    #expect(model.saving && model.enabled && !model.confirmationRequired)
    await model.load(); await model.requestChange(to: false); await model.confirm()
    #expect(gate.requestCount == 2)
    try expectBody(gate.requests[1], value: true)
    gate.releaseAll(); await save.value
    #expect(!model.saving && !model.enabled) // The engine response is authoritative.
  }

  @Test func connectScreenReusesSharedInstanceAndKeepsTheSwitchesIndependent() async throws {
    let stub = StubTransport([.init(status: 200, body: #"{"source_access":true,"processing_tools":false}"#),
      .init(status: 200, body: #"{"source_access":true,"processing_tools":false}"#),
      .init(status: 200, body: #"{"source_access":true,"processing_tools":true}"#)])
    let shared = model(stub)
    let source = SourceAccessModel(api: api(stub))
    let tools = ConnectAIToolsModel(api: api(stub), sourceAccess: source, processingTools: shared,
      copy: { _ in true }, open: { _ in true })
    #expect(tools.processingTools === shared && tools.sourceAccess === source)
    await source.load(); await shared.load()
    #expect(source.enabled && !shared.enabled)
    await tools.processingTools.requestChange(to: true); await tools.processingTools.confirm()
    #expect(shared.enabled && source.enabled)
    try expectBody(stub.requests[2], value: true)
    let reopened = ConnectAIToolsModel(api: api(stub), sourceAccess: source, processingTools: shared,
      copy: { _ in true }, open: { _ in true })
    #expect(reopened.processingTools === shared && reopened.processingTools.enabled)
  }
}
