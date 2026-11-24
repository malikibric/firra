import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct ConnectAIToolsTests {
  func fixture() throws -> String {
    try String(contentsOf: #require(Bundle.module.url(forResource: "mcp-connect", withExtension: "json",
      subdirectory: "Fixtures/LocalRoutes")), encoding: .utf8)
  }
  func api(_ stub: StubTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: stub, tokenProvider: { "synthetic-engine-secret" })
  }

  @Test func capturedRouteDecodesAndEachActionReceivesExactlyItsValue() async throws {
    let body = try fixture()
    let stub = StubTransport([.init(status: 200, body: body)])
    var copied: [String] = []
    var opened: [URL] = []
    let model = ConnectAIToolsModel(api: api(stub), copy: { copied.append($0); return true },
      open: { opened.append($0); return true })
    await model.load()
    #expect(!model.loading && model.error == nil)
    #expect(model.items.map(\.id) == ["claude_code", "codex", "cursor", "vscode", "claude_desktop"])
    #expect(stub.requests[0].url?.path == "/local/mcp/connect")
    for item in model.items { model.act(on: item) }
    #expect(copied == model.items.filter { $0.kind != .deeplink }.map(\.value))
    #expect(opened.map(\.absoluteString) == model.items.filter { $0.kind == .deeplink }.map(\.value))
    #expect(model.actionMessage == "Copied.")
    assertNoToken(body)
    for value in copied + opened.map(\.absoluteString) {
      assertNoToken(value)
    }
    let cursor = try #require(opened.first)
    let encoded = try #require(URLComponents(url: cursor, resolvingAgainstBaseURL: false)?.queryItems?
      .first(where: { $0.name == "config" })?.value)
    let config = try #require(Data(base64Encoded: encoded))
    assertNoToken(String(decoding: config, as: UTF8.self))
    let vscode = try #require(opened.last)
    assertNoToken(try #require(vscode.query?.removingPercentEncoding))
  }

  private func assertNoToken(_ value: String) {
    #expect(!value.contains("synthetic-engine-secret"))
    #expect(!value.lowercased().contains("authorization"))
    #expect(!value.lowercased().contains("bearer"))
    #expect(!value.lowercased().contains("access_token"))
    #expect(!value.lowercased().contains("refresh_token"))
    #expect(!value.lowercased().contains("engine_token"))
    #expect(value.range(of: #"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"#,
      options: .regularExpression) == nil)
  }

  @Test func loadingIsVisibleAndConcurrentLoadsAreIgnored() async throws {
    let gate = GatedTransport(reply: .init(status: 200, body: try fixture()))
    let api = APIClient(base: testBaseURL, transport: gate, tokenProvider: { "synthetic-engine-secret" })
    let model = ConnectAIToolsModel(api: api, copy: { _ in true }, open: { _ in true })
    let load = Task { await model.load() }
    await gate.waitForRequests(1)
    #expect(model.loading && model.items.isEmpty)
    await model.load(); #expect(gate.requestCount == 1)
    gate.releaseAll(); await load.value
    #expect(!model.loading && model.items.count == 5)
  }

  @Test func loadFailureHasSafeTextAndRetryReplacesIt() async throws {
    let stub = StubTransport([.init(status: 500, body: #"{"message":"synthetic-engine-secret"}"#),
      .init(status: 200, body: try fixture())])
    let model = ConnectAIToolsModel(api: api(stub), copy: { _ in true }, open: { _ in true })
    await model.load()
    #expect(model.items.isEmpty && model.error != nil && !model.loading)
    assertNoToken(model.error!)
    await model.load()
    #expect(model.items.count == 5 && model.error == nil && !model.loading)
  }

  @Test func connectionTestReportsReachabilityAndCanBeRetriedWithoutShowingStatusData() async {
    let stub = StubTransport([.init(status: 500, body: #"{"message":"synthetic-engine-secret"}"#),
      .init(status: 200, body: #"{"engine_version":"synthetic-engine-secret","database":"ready","connections":[]}"#)])
    let model = ConnectAIToolsModel(api: api(stub), copy: { _ in true }, open: { _ in true })
    await model.testConnection()
    #expect(model.connectionMessage?.contains("could not be reached") == true && !model.testing)
    assertNoToken(model.connectionMessage!)
    await model.testConnection()
    #expect(model.connectionMessage == "The local engine is running." && !model.testing)
    #expect(stub.requests.allSatisfy { $0.url?.path == "/local/status" })
  }

  @Test func failedNativeActionsExplainHowToRetry() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture())])
    let model = ConnectAIToolsModel(api: api(stub), copy: { _ in false }, open: { _ in false })
    await model.load()
    model.act(on: model.items[0]); #expect(model.actionMessage == "Could not copy. Try again.")
    model.act(on: model.items[2]); #expect(model.actionMessage?.contains("Check that it is installed") == true)
  }

  @Test func unexpectedInstallProtocolIsNeverOpened() async {
    let body = #"{"server_name":"firra","items":[{"id":"cursor","title":"Cursor","kind":"deeplink","value":"https://example.test/"}]}"#
    let model = ConnectAIToolsModel(api: api(StubTransport([.init(status: 200, body: body)])),
      copy: { _ in Issue.record("Must not copy an install link"); return false },
      open: { _ in Issue.record("Must not open an unexpected protocol"); return false })
    await model.load(); model.act(on: model.items[0])
    #expect(model.actionMessage == "This install link is unavailable.")
  }
}
