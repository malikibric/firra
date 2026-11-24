import Foundation
import Testing
@testable import FirraKit

private struct RelayCreateGate: HTTPTransport {
  let discovery: StubTransport
  let creation: GatedTransport
  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    if request.httpMethod == "POST" { return try await creation.send(request) }
    return try await discovery.send(request)
  }
}

@MainActor @Suite struct RelayLinksTests {
  func fixture(_ name: String) throws -> String {
    try String(contentsOf: #require(Bundle.module.url(forResource: name, withExtension: "json",
      subdirectory: "Fixtures/LocalRoutes")), encoding: .utf8)
  }
  func api(_ transport: any HTTPTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: transport, tokenProvider: { "synthetic-engine-token" })
  }
  func model(_ transport: any HTTPTransport, copy: @escaping (String) -> Bool = { _ in true }) -> RelayLinksModel {
    RelayLinksModel(api: api(transport), copy: copy)
  }

  @Test func noAddressRefusesCreationAndSavingUsesTheRealRouteShape() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture("relay-unconfigured")),
      .init(status: 200, body: try fixture("relay-configured"))])
    let model = model(stub)
    #expect(model.name == "claude.ai" && !model.sources)
    await model.load(); await model.create()
    #expect(model.error == "Set the relay address first." && stub.requests.count == 1)
    #expect(model.settings?.state == .notRegistered)
    model.address = " https://relay.example \n"; await model.saveAddress()
    #expect(model.settings?.relayUrl == "https://relay.example" && model.address.isEmpty && model.error == nil)
    let data = try #require(stub.requests[1].httpBody)
    #expect(try JSONSerialization.jsonObject(with: data) as? [String: String] == ["relay_url":"https://relay.example"])
    #expect(stub.requests[1].httpMethod == "PUT" && stub.requests[1].url?.path == "/local/relay")
  }

  @Test(arguments: [false, true]) func scopesAreExactAndSecretIsShownOnceAndCopiedOnlyOnClick(sources: Bool) async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture("relay-configured")),
      .init(status: 200, body: try fixture("relay-link-created")), .init(status: 200, body: try fixture("relay-links")),
      .init(status: 200, body: try fixture("relay-links"))])
    var copied: [String] = []
    let model = model(stub, copy: { copied.append($0); return true })
    await model.load(); model.sources = sources; await model.create()
    let url = try #require(model.createdURL)
    #expect(url == "https://relay.example/m/0000000000000001.synthetic-fixture-secret" && copied.isEmpty)
    #expect(model.settings?.links.count == 1 && !model.sources)
    let data = try #require(stub.requests[1].httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    #expect(body["scopes"] as? [String] == (sources ? ["intelligence", "sources"] : ["intelligence"]))
    #expect(body["name"] as? String == "claude.ai" && body.count == 2)
    #expect(stub.requests[1].url?.path == "/local/relay/links" && stub.requests[1].httpMethod == "POST")
    #expect(stub.requests[2].url?.path == "/local/relay" && stub.requests[2].httpMethod == "GET")
    await model.create(); #expect(stub.requests.count == 3)
    model.copyCreatedLink(); #expect(copied == [url])
    model.dismissCreatedLink(); model.copyCreatedLink()
    #expect(model.createdURL == nil && model.copyMessage == nil && copied == [url])
    await model.load(); #expect(model.createdURL == nil)
    #expect(!String(describing: model.settings).contains("synthetic-fixture-secret"))
    #expect(!String(describing: model.settings).contains("hash"))
  }

  @Test func leavingTheScreenBeforeTheCreateReplyDiscardsTheSecret() async throws {
    let gate = GatedTransport(reply: .init(status: 200, body: try fixture("relay-link-created")))
    let discovery = StubTransport([.init(status: 200, body: try fixture("relay-configured")),
      .init(status: 200, body: try fixture("relay-links"))])
    let model = model(RelayCreateGate(discovery: discovery, creation: gate), copy: { _ in
      Issue.record("Secret must not be copied implicitly"); return false
    })
    await model.load()
    let create = Task { await model.create() }
    await gate.waitForRequests(1)
    #expect(model.busy && model.createdURL == nil)
    model.dismissCreatedLink()
    await model.create(); await model.load(); #expect(gate.requestCount == 1)
    gate.releaseAll(); await create.value
    #expect(model.createdURL == nil && !model.busy && model.settings?.links.count == 1)
  }

  @Test func revokeRequiresConfirmationAndRefreshesWithoutSecrets() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture("relay-links")),
      .init(status: 204, body: ""), .init(status: 200, body: try fixture("relay-configured"))])
    let model = model(stub)
    await model.load()
    let link = try #require(model.settings?.links.first)
    #expect(link.scopeText == "Client intelligence" && MenuStatusModel.syncDate(link.createdAt) != nil)
    model.requestRevoke(link); #expect(stub.requests.count == 1 && model.revoking?.id == link.id)
    model.cancelRevoke(); await model.confirmRevoke(); #expect(stub.requests.count == 1)
    model.requestRevoke(link); await model.confirmRevoke()
    #expect(stub.requests[1].httpMethod == "DELETE" && stub.requests[1].url?.path == "/local/relay/links/\(link.id)")
    #expect(stub.requests[2].httpMethod == "GET" && model.settings?.links.isEmpty == true && model.revoking == nil)
  }

  @Test(arguments: [409, 422, 500]) func serverErrorsAreShortAndNeverEchoSecretBodies(status: Int) async throws {
    let failure = StubTransport.Reply(status: status,
      body: #"{"error":"invalid_request","message":"private-secret-url","request_id":"test"}"#)
    let stub = StubTransport([failure, .init(status: 200, body: try fixture("relay-configured")), failure,
      .init(status: 200, body: try fixture("relay-links")), failure])
    let model = model(stub)
    model.address = "invalid"; await model.saveAddress()
    #expect(model.error == (status == 422 ? "Enter the https relay address Firra gave you, without a path." :
      status == 409 ? "Set the relay address and wait for Firra to connect, then try again." :
      "Firra could not complete the request. Try again."))
    await model.load(); await model.create()
    #expect(model.createdURL == nil && !model.busy && model.error?.contains("private") == false)
    if status == 422 { #expect(model.error == "Check the link name and permissions, then try again.") }
    await model.load(); model.requestRevoke(try #require(model.settings?.links.first)); await model.confirmRevoke()
    #expect(!model.busy && model.error != nil && model.error?.contains("private") == false)
  }

  @Test func networkErrorsAndCopyFailureAreSafeAndRetryable() async throws {
    struct Offline: HTTPTransport {
      func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) { throw URLError(.cannotConnectToHost) }
    }
    let offline = model(Offline())
    await offline.load(); #expect(offline.error == "Relay status could not be loaded. Try again.")
    await offline.saveAddress(); #expect(offline.error == "Firra could not complete the request. Try again.")
    let stub = StubTransport([.init(status: 200, body: try fixture("relay-configured")),
      .init(status: 200, body: try fixture("relay-link-created")), .init(status: 500, body: "{}")])
    let model = model(stub, copy: { _ in false })
    await model.load(); model.name = " "; await model.create(); #expect(stub.requests.count == 1)
    model.name = "claude.ai"; await model.create()
    #expect(model.createdURL != nil && model.error == "Link created, but the list could not be refreshed. Try again.")
    model.copyCreatedLink(); #expect(model.copyMessage == "Could not copy. Try again.")
    model.dismissCreatedLink(); #expect(model.createdURL == nil)
  }

  @Test func statusWordsDoNotExposeStateInternals() {
    #expect(RelaySettings.State.online.text == "Reachable from claude.ai")
    #expect(RelaySettings.State.offline.text == "Firra cannot reach the relay")
    #expect(RelaySettings.State.connecting.text == "Connecting to the relay…")
    #expect(RelaySettings.State.notRegistered.text == "Firra is not registered with the relay yet")
  }
}
