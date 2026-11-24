import Foundation
import Testing
@testable import FirraKit

@Suite struct APIClientTests {
  func client(_ replies: [StubTransport.Reply]) -> (APIClient, StubTransport) {
    let stub = StubTransport(replies)
    return (APIClient(base: URL(string: "https://api.example")!, transport: stub, tokenProvider: { "tok" }), stub)
  }

  @Test func decodesAttentionAndSendsTheBearerToken() async throws {
    let body = """
    {"generated_at":"2026-10-02T09:00:00.000Z","items":[{"client_id":"c1","name":"Redfern","status":"active",
     "policy":"full","as_of":"2026-10-01","risk_state":"high","previous_risk_state":"elevated",
     "state_since":"2026-09-25","expansion_state":"none","strength":"moderate","play":"fix_first",
     "days_to_notice":24,"drivers":[{"type":"delivery_complaint","family":"delivery","severity":"high",
     "basis":"evidence","occurred_at":"2026-09-30","primary":true,"evidence_count":1}]}]}
    """
    let (api, stub) = client([.init(status: 200, body: body)])
    let result = try await api.attention(workspaceId: "w1")
    #expect(result.items.first?.name == "Redfern")
    #expect(result.items.first?.drivers.first?.evidenceCount == 1)
    let request = try #require(stub.requests.first)
    #expect(request.url?.path == "/v1/workspaces/w1/attention")
    #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
  }

  @Test func serverErrorsCarryTheErrorModel() async {
    let body = #"{"error":"forbidden","message":"Owner or admin access is required.","request_id":"r-1"}"#
    let (api, _) = client([.init(status: 403, body: body)])
    await #expect(throws: APIError.server(status: 403, code: "forbidden", message: "Owner or admin access is required.", requestID: "r-1")) {
      _ = try await api.attention(workspaceId: "w1")
    }
  }

  @Test func a401RejectsTheEngineTokenAndNonsenseIsATransportError() async {
    let (api, _) = client([.init(status: 401, body: #"{"error":"unauthorized","message":"x","request_id":"r"}"#),
                           .init(status: 200, body: "not json")])
    await #expect(throws: APIError.unauthenticated) { _ = try await api.me() }
    await #expect(throws: APIError.transport("unexpected answer shape")) { _ = try await api.me() }
  }

  @Test func userMessagesNeverLeakDetails() {
    #expect(APIError.transport("socket 10.0.0.1 refused").userMessage.contains("10.0.0.1") == false)
    #expect(APIError.server(status: 503, code: "reads_not_configured", message: "m", requestID: "r").userMessage.contains("not set up"))
  }
}

@MainActor @Suite struct ViewModelTests {
  func api(_ replies: [StubTransport.Reply]) -> APIClient {
    APIClient(base: URL(string: "https://api.example")!, transport: StubTransport(replies), tokenProvider: { "tok" })
  }

  @Test func attentionSummaryIsReadable() async {
    let item = #"{"client_id":"c","name":"A","status":"active","policy":"full","as_of":"2026-10-01","risk_state":"high","previous_risk_state":null,"state_since":"2026-09-25","expansion_state":"none","strength":"weak","play":null,"days_to_notice":null,"drivers":[]}"#
    let model = AttentionModel(api: api([.init(status: 200, body: #"{"generated_at":"x","items":[\#(item),\#(item)]}"#)]))
    #expect(model.summary == "Checking your clients…")  // never "nobody" before the first answer
    await model.load(workspaceId: "w")
    #expect(model.summary == "2 clients need attention" && model.phase == .ready)
  }

}
