import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct SlackConnectTests {
  let status = #"{"engine_version":"test","database":"ready","connections":[{"id":"slack-id","provider":"slack","account_label":"Synthetic workspace","state":"active","last_synced_at":"2026-10-05T10:00:00Z"},{"id":"google-id","provider":"gmail","account_label":"Synthetic Google","state":"active"}]}"#
  let clients = #"{"items":[{"id":"client-a","name":"Alpha","status":"active","policy":"full","domains":["alpha.test"]},{"id":"excluded","name":"Excluded","status":"active","policy":"excluded","domains":[]}]}"#
  let channels = #"{"items":[{"channel_id":"C1","label":"alpha","kind":"public","suggested_client_id":"client-a","reason":"channel_name"},{"channel_id":"C2","label":"external","kind":"public","suggested_client_id":"client-a","reason":"slack_connect"},{"channel_id":"C3","label":"internal","kind":"private","suggested_client_id":null,"reason":"unmatched"},{"channel_id":"D1","label":"dm","kind":"direct","suggested_client_id":"client-a","reason":"member_domain"},{"channel_id":"D2","label":"unmatched dm","kind":"direct","suggested_client_id":null,"reason":"unmatched"},{"channel_id":"C4","label":"excluded","kind":"public","suggested_client_id":"excluded","reason":"channel_name"}]}"#
  func api(_ stub: StubTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: stub, tokenProvider: { "synthetic-engine-token" })
  }
  func discovery() -> [StubTransport.Reply] {
    [.init(status: 200, body: status), .init(status: 200, body: clients), .init(status: 200, body: channels)]
  }
  @Test func manifestLinkRoundTripsEngineFixture() throws {
    let fixture = try Data(contentsOf: #require(Bundle.module.url(forResource: "slack-manifest", withExtension: "json", subdirectory: "Fixtures")))
    let query = try #require(URLComponents(url: SlackManifest.creationURL, resolvingAgainstBaseURL: false)?.queryItems)
    #expect(SlackManifest.creationURL.host == "api.slack.com")
    #expect(query.first { $0.name == "new_app" }?.value == "1")
    let encoded = try #require(query.first { $0.name == "manifest_json" }?.value)
    let expected = try JSONSerialization.jsonObject(with: fixture) as! NSDictionary
    #expect(try JSONSerialization.jsonObject(with: Data(encoded.utf8)) as! NSDictionary == expected)
    #expect(try JSONSerialization.jsonObject(with: Data(SlackManifest.json.utf8)) as! NSDictionary == expected)
  }
  @Test func tokenGoesThroughControlBeforeHTTPAndIsNeverStoredInModel() async throws {
    let stub = StubTransport([.init(status: 200, body: #"{"connection_id":"generated","team_id":"T1"}"#)] + discovery())
    var slot: String?
    let model = SlackConnectModel(api: api(stub), workspace: "workspace") { value, name in
      #expect(value == "xoxp-synthetic-secret" && stub.requests.isEmpty)
      slot = name
    }
    await model.connect(token: "xoxp-synthetic-secret", accountLabel: "Synthetic workspace")
    let body = try #require(JSONSerialization.jsonObject(with: stub.requests[0].httpBody!) as? [String: String])
    let id = try #require(body["connection_id"])
    #expect(UUID(uuidString: id) != nil && slot == SecretSlot.slackUserToken(id))
    #expect(body == ["connection_id": id, "account_label": "Synthetic workspace"])
    #expect(!String(describing: model).contains("xoxp-synthetic-secret"))
    #expect(!Mirror(reflecting: model).children.contains { ($0.label ?? "").lowercased().contains("token") })
    #expect(stub.requests.allSatisfy { !String(decoding: $0.httpBody ?? Data(), as: UTF8.self).contains("xoxp-") })
    #expect(model.choices.count == 5 && model.clients.count == 1)
  }
  @Test func suggestionsConsentAndScopePayload() async throws {
    let stub = StubTransport(discovery() + [.init(status: 200, body: #"{"status":"queued"}"#),
      .init(status: 200, body: status), .init(status: 200, body: clients)])
    let model = SlackConnectModel(api: api(stub), workspace: "workspace", putSecret: { _, _ in })
    await model.remap("slack-id")
    #expect(model.choices[0].clientId == "client-a" && !model.choices[0].included)
    #expect(model.choices[1].included && model.choices[3].included)
    #expect(model.choices[4].clientId == nil)
    await model.save(); #expect(stub.requests.count == 3 && model.message?.contains("colleagues") == true)
    model.choices[0].colleagueConsent = true
    model.choices[2].clientId = "client-a"
    await model.save(); #expect(stub.requests.count == 3)
    model.choices[2].clientId = nil
    await model.save()
    let body = try #require(JSONSerialization.jsonObject(with: stub.requests[3].httpBody!) as? [String: Any])
    let scope = try #require(body["channels"] as? [[String: Any]])
    #expect(scope.count == 6 && scope[0]["external_id"] as? String == "C1")
    #expect(scope[0]["client_id"] as? String == "client-a" && scope[0]["include"] as? Bool == true)
    #expect(scope[2]["include"] as? Bool == false && scope[2]["client_id"] is NSNull)
    #expect(scope[5]["external_id"] as? String == "D2" && scope[5]["include"] as? Bool == false)
    #expect(scope[0]["label"] as? String == "alpha")
    #expect((scope[0]["meta"] as? [String: String]) == ["kind":"public"])
    #expect(stub.requests[3].httpMethod == "PUT" && stub.requests[3].url?.path == "/local/connections/slack/slack-id/scope")
    #expect(model.mappingConnection == nil && model.message == nil)
  }
  @Test func invalidTokenAndControlFailureNeverSendHTTP() async {
    let stub = StubTransport([])
    var calls = 0
    let model = SlackConnectModel(api: api(stub), workspace: "workspace") { _, _ in
      calls += 1; throw CocoaError(.fileWriteUnknown)
    }
    await model.connect(token: "xoxb-synthetic", accountLabel: "Slack")
    #expect(calls == 0 && stub.requests.isEmpty && model.message?.contains("User OAuth Token") == true)
    await model.connect(token: "xoxp-synthetic", accountLabel: " ")
    #expect(calls == 0 && stub.requests.isEmpty)
    await model.connect(token: "xoxp-synthetic", accountLabel: "Slack")
    #expect(calls == 1 && stub.requests.isEmpty && !model.busy && model.message != nil)
  }
  @Test(arguments: ["not_a_user_token", "token_invalid", "missing_scopes", "scopes_too_broad",
    "account_already_connected", "missing_secret", "connection_conflict", "connection_unavailable",
    "slack_unreachable", "slack_unavailable", "slack_unreadable", "slack_refused"])
  func errorsHaveSafeSpecificMessages(code: String) async {
    let stub = StubTransport([.init(status: code == "account_already_connected" || code == "missing_secret" ? 409 : 422,
      body: "{\"error\":\"\(code)\",\"message\":\"private token detail\",\"request_id\":\"test\"}")])
    let model = SlackConnectModel(api: api(stub), workspace: "workspace", putSecret: { _, _ in })
    await model.connect(token: "xoxp-synthetic", accountLabel: "Slack")
    #expect(model.message != nil && model.message?.contains("private") == false && !model.busy)
    let expected = [
      "not_a_user_token": "Paste the User OAuth Token starting with xoxp-.",
      "token_invalid": "This token is expired or invalid. Install the app again and copy a new token.",
      "missing_scopes": "Required read permissions are missing. Create the app from the Firra manifest.",
      "scopes_too_broad": "This token has extra permissions. Use only the read permissions in the Firra manifest.",
      "account_already_connected": "This Slack workspace is already connected.",
      "missing_secret": "The engine did not receive the token. Paste it and try again.",
      "connection_conflict": "This connection could not be created. Try again.",
      "connection_unavailable": "Reconnect this Slack workspace.",
      "slack_unreachable": "Slack is unavailable. Try again shortly.",
      "slack_unavailable": "Slack is unavailable. Try again shortly.",
    ]
    #expect(model.message == (expected[code] ?? "Slack could not complete the request. Try again."))
  }
  @Test func statusSyncAndConfirmedDisconnect() async {
    let stub = StubTransport([.init(status: 200, body: status), .init(status: 200, body: clients),
      .init(status: 200, body: #"{"status":"queued"}"#), .init(status: 200, body: status), .init(status: 200, body: clients),
      .init(status: 204, body: ""), .init(status: 200, body: #"{"engine_version":"test","database":"ready","connections":[]}"#),
      .init(status: 200, body: clients)])
    let model = SlackConnectModel(api: api(stub), workspace: "workspace", putSecret: { _, _ in })
    await model.load()
    #expect(model.connections.count == 1 && model.connections[0].state == "active")
    #expect(MenuStatusModel.syncDate(model.connections[0].lastSyncedAt) != nil)
    await model.sync("slack-id")
    #expect(stub.requests[2].url?.path == "/local/connections/slack-id/sync")
    await model.disconnect(); #expect(stub.requests.count == 5)
    model.disconnecting = "slack-id"; model.disconnecting = nil // cancelled confirmation
    await model.disconnect(); #expect(stub.requests.count == 5)
    model.disconnecting = "slack-id"; await model.disconnect()
    #expect(stub.requests[5].httpMethod == "DELETE")
    #expect(stub.requests[5].url?.path == "/local/connections/slack/slack-id")
    #expect(model.connections.isEmpty && model.disconnecting == nil)
  }
}
