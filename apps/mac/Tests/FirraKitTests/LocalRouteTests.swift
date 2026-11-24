import Foundation
import Testing
@testable import FirraKit

@Suite struct LocalRouteDecodingTests {
  func decode<T: Decodable>(_ name: String, as type: T.Type) throws -> T {
    let url = try #require(Bundle.module.url(forResource: name, withExtension: "json",
      subdirectory: "Fixtures/LocalRoutes"))
    return try JSONDecoder.api.decode(type, from: Data(contentsOf: url))
  }

  @Test func responsesCapturedFromSyntheticEngineRoutesDecode() throws {
    #expect(try decode("me", as: MeResponse.self).memberships.count == 1)
    #expect(try decode("status", as: LocalStatus.self).database == "ready")
    #expect(try decode("attention", as: AttentionResponse.self).items.count == 1)
    _ = try decode("changes", as: ChangesResponse.self)
    #expect(try decode("meetings", as: MeetingsResponse.self).items.count == 1)
    #expect(try decode("clients", as: ClientListResponse.self).items.count == 1)
    #expect(try decode("detail", as: ClientDetailResponse.self).client.name == "Synthetic Acme")
    #expect(try decode("timeline", as: TimelinePage.self).items.count > 0)
    #expect(try decode("evidence-page", as: EvidencePage.self).items.count == 1)
    #expect(try decode("evidence", as: EvidenceItem.self).quote == "Synthetic verified quote")
    #expect(try decode("decisions", as: DecisionsPage.self).items.count == 1)
    #expect(try decode("routines", as: RoutinesResponse.self).items.count == 3)
    #expect(try decode("setup-clients", as: SetupClientsResponse.self).items[0].domains == ["acme.example"])
    #expect(try decode("services", as: ServicesResponse.self).items[0].name == "Synthetic SEO")
    #expect(try decode("replay-runs", as: ReplayRunsResponse.self).items.count == 1)
    #expect(try decode("replay", as: ReplayClientResult.self).narrative.maxState == "high")
    #expect(try decode("audit", as: AuditResponse.self).items.count > 0)
    for write in ["feedback", "outcome", "routine-enable", "service-create"] {
      #expect(try !decode(write, as: APIClient.IdResponse.self).id.isEmpty)
    }
  }
}

@MainActor @Suite struct LocalConnectionsTests {
  let status = #"{"engine_version":"test","database":"ready","connections":[{"id":"gmail","provider":"gmail","account_label":"synthetic@acme.example","state":"needs_reauth","last_synced_at":null,"error_class":"invalid_grant"},{"id":"slack","provider":"slack","account_label":"Synthetic Slack","state":"idle"}]}"#

  @Test func readsStatusReconnectsAndDisconnectsThroughTheExistingLocalRoutes() async throws {
    let stub = StubTransport([.init(status: 200, body: status),
      .init(status: 200, body: status), .init(status: 204, body: ""),
      .init(status: 200, body: #"{"engine_version":"test","database":"ready","connections":[]}"#)])
    let api = APIClient(base: testBaseURL, transport: stub, tokenProvider: { "local-token" })
    var reconnected: String?
    let model = ConnectionsModel(api: api, reconnect: { reconnected = $0.id })
    await model.load(workspaceId: "workspace")
    #expect(model.connections.count == 1 && model.connections[0].state == "needs_reauth")
    await model.reconnect(workspaceId: "workspace", connection: try #require(model.connections.first))
    #expect(reconnected == "gmail")
    await model.disconnect(workspaceId: "workspace", connectionId: "gmail")
    #expect(model.connections.isEmpty)
    #expect(stub.requests[0].url?.path == "/local/status")
    #expect(stub.requests[2].url?.path == "/v1/workspaces/workspace/connections/gmail")
    #expect(stub.requests[2].httpMethod == "DELETE")
  }

  @Test func reconnectPostsOnlyTheSecretSlotIdAndAccountLabel() async throws {
    let stub = StubTransport([.init(status: 200,
      body: #"{"gmail_connection_id":"g","calendar_connection_id":"c"}"#)])
    let api = APIClient(base: testBaseURL, transport: stub, tokenProvider: { "local-token" })
    try await api.connectGoogle(connectionId: "root-id", accountLabel: "synthetic@acme.example")
    let request = try #require(stub.requests.first)
    #expect(request.url?.path == "/local/connections/google" && request.httpMethod == "POST")
    let data = try #require(request.httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    #expect(Set(body.keys) == ["connection_id", "account_label", "backfill_months"])
    #expect(body["connection_id"] as? String == "root-id")
  }
}

@Suite struct GoogleOAuthConfigLoaderTests {
  @Test func readsDownloadedDesktopJsonAndReportsMissingOrInvalidFiles() throws {
    let home = FileManager.default.temporaryDirectory.appending(path: "firra-google-config-\(UUID())")
    try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: home) }
    #expect(throws: GoogleOAuthConfigError.missing) { try loadGoogleOAuthConfig(home: home) }
    let file = home.appending(path: "google-oauth-client.json")
    try Data(#"{"installed":{"client_id":"synthetic-client","client_secret":"synthetic-secret"}}"#.utf8).write(to: file)
    #expect(try loadGoogleOAuthConfig(home: home) == GoogleOAuthConfig(clientID: "synthetic-client", clientSecret: "synthetic-secret"))
    try Data(#"{"web":{"client_id":"wrong-type"}}"#.utf8).write(to: file)
    #expect(throws: GoogleOAuthConfigError.invalid) { try loadGoogleOAuthConfig(home: home) }
    #expect(GoogleOAuthConfigError.missing.userMessage.contains("google-oauth-client.json"))
  }
}

@MainActor @Suite struct LocalClientStatusTests {
  @Test func preservesEachClientStatusQuery() async throws {
    let stub = StubTransport(Array(repeating: .init(status: 200, body: #"{"items":[]}"#), count: 3))
    let api = APIClient(base: testBaseURL, transport: stub, tokenProvider: { "local-token" })
    for status in ["active", "churned", "all"] {
      _ = try await api.clients(workspaceId: "workspace", status: status)
    }
    #expect(stub.requests.map { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?.query } ==
      ["status=active", "status=churned", "status=all"])
  }
}
