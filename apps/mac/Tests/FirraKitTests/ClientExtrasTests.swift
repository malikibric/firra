import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct ClientExtrasTests {
  private let emptyPage = #"{"items":[],"next_cursor":null}"#

  private func fixture(enriched: Bool = true, clientId: String = "c1") throws -> String {
    var response = try #require(JSONSerialization.jsonObject(with: Data(ClientDetailTests().detail.utf8)) as? [String: Any])
    var client = try #require(response["client"] as? [String: Any])
    client["id"] = clientId
    if enriched {
      var people = try #require(client["stakeholders"] as? [[String: Any]])
      people[0]["is_champion"] = true
      client["stakeholders"] = people
      client["unknowns"] = [["code": "budget_unknown", "detail": "Budget not stated."]]
      for key in ["commitments", "goals", "needs"] {
        // The original fixture has no goals or needs; supply a synthetic claim of each kind.
        var rows = try #require(client[key] as? [[String: Any]])
        if key == "goals" { rows = [["text": "Grow leads", "metric": NSNull(), "stated_at": "2026-10-01"]] }
        if key == "needs" { rows = [["topic": "Reporting", "mention_count": 3, "thread_count": 2, "last_seen_at": "2026-10-01"]] }
        rows[0]["evidence_ids"] = ["cached", "gone", "missing"]
        client[key] = rows
      }
    }
    response["client"] = client
    return String(decoding: try JSONSerialization.data(withJSONObject: response), as: UTF8.self)
  }

  @Test func olderResponsesDecodeWithFalseChampionAndEmptyExtras() throws {
    let client = try JSONDecoder.api.decode(ClientDetailResponse.self, from: Data(fixture(enriched: false).utf8)).client
    #expect(!client.stakeholders[0].isChampion && client.unknowns.isEmpty)
    #expect(client.commitments[0].evidenceIds.isEmpty)
    let goal = try JSONDecoder.api.decode(GoalItem.self, from: Data(#"{"text":"Grow leads","stated_at":"2026-10-01"}"#.utf8))
    let need = try JSONDecoder.api.decode(NeedItem.self, from: Data(#"{"topic":null,"mention_count":3,"thread_count":2,"last_seen_at":"2026-10-01"}"#.utf8))
    #expect(goal.evidenceIds.isEmpty && need.evidenceIds.isEmpty)
  }

  @Test func newResponsesDecodeChampionUnknownsAndAllClaimEvidenceIds() throws {
    let client = try JSONDecoder.api.decode(ClientDetailResponse.self, from: Data(fixture().utf8)).client
    #expect(client.stakeholders[0].isChampion)
    #expect(client.unknowns.map(\.code) == ["budget_unknown"])
    #expect(client.unknowns.map(\.detail) == ["Budget not stated."])
    #expect(client.commitments[0].evidenceIds == ["cached", "gone", "missing"])
    #expect(client.goals[0].evidenceIds == client.commitments[0].evidenceIds)
    #expect(client.needs[0].evidenceIds == client.commitments[0].evidenceIds)
    let person = try JSONDecoder.api.decode(Stakeholder.self, from: Data(#"{"is_decision_maker":false,"is_champion":false}"#.utf8))
    #expect(!person.isChampion)
  }

  @Test func explicitEmptyUnknownsAndEvidenceArraysDecode() throws {
    let body = try fixture().replacingOccurrences(of: #"["cached","gone","missing"]"#, with: "[]")
    var response = try #require(JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any])
    var client = try #require(response["client"] as? [String: Any])
    client["unknowns"] = [] as [String]
    response["client"] = client
    let decoded = try JSONDecoder.api.decode(ClientDetailResponse.self, from: JSONSerialization.data(withJSONObject: response)).client
    #expect(decoded.unknowns.isEmpty)
    #expect(decoded.commitments[0].evidenceIds.isEmpty && decoded.goals[0].evidenceIds.isEmpty && decoded.needs[0].evidenceIds.isEmpty)
  }

  @Test func claimsReuseQuotesAndOneFailureDoesNotHideOtherQuotes() async throws {
    let cached = #"{"id":"cached","quote":"Already here.","occurred_at":"2026-10-01"}"#
    let missing = #"{"id":"missing","quote":"Exact quote — déjà vu.","occurred_at":"2026-10-01"}"#
    let stub = StubTransport([
      .init(status: 200, body: try fixture()), .init(status: 200, body: emptyPage),
      .init(status: 200, body: #"{"items":[\#(cached)],"next_cursor":null}"#),
      .init(status: 404, body: "{}"), .init(status: 200, body: missing),
    ])
    let model = ClientDetailModel(api: APIClient(base: testBaseURL, transport: stub, tokenProvider: { "tok" }))
    await model.load(workspaceId: "w", clientId: "c1")
    let detail = try #require(model.detail)
    for ids in [detail.commitments[0].evidenceIds, detail.goals[0].evidenceIds, detail.needs[0].evidenceIds] {
      await model.loadEvidence(workspaceId: "w", evidenceIds: ids)
      #expect(model.quotes(evidenceIds: ids).map(\.quote) == ["Already here.", "Exact quote — déjà vu."])
    }
    #expect(stub.requests.count == 5 && model.evidence["gone"] == nil && model.phase == .ready)
    #expect(stub.requests[3].url?.path == "/v1/workspaces/w/evidence/gone")
    #expect(stub.requests[4].url?.path == "/v1/workspaces/w/evidence/missing")
    await model.loadEvidence(workspaceId: "w", evidenceIds: [])
    #expect(stub.requests.count == 5)
  }

  @Test func switchingClientIgnoresALateQuoteAnswer() async throws {
    let transport = ExtrasGatedTransport(replies: [
      .init(status: 200, body: try fixture()), .init(status: 200, body: emptyPage), .init(status: 200, body: emptyPage),
      .init(status: 200, body: try fixture(enriched: false, clientId: "c2")),
      .init(status: 200, body: emptyPage), .init(status: 200, body: emptyPage),
    ])
    let model = ClientDetailModel(api: APIClient(base: testBaseURL, transport: transport, tokenProvider: { "tok" }))
    await model.load(workspaceId: "w", clientId: "c1")
    let loading = Task { await model.loadEvidence(workspaceId: "w", evidenceIds: ["missing", "later"]) }
    await transport.gated.waitForRequests(1)
    await model.load(workspaceId: "w", clientId: "c2")
    transport.gated.releaseAll()
    await loading.value
    #expect(model.detail?.id == "c2" && model.evidence.isEmpty)
    #expect(transport.gated.requestCount == 1)
  }
}

private final class ExtrasGatedTransport: HTTPTransport, Sendable {
  let initial: StubTransport
  let gated = GatedTransport(reply: .init(status: 200, body: #"{"id":"missing","quote":"Old client quote.","occurred_at":"2026-10-01"}"#))
  init(replies: [StubTransport.Reply]) { initial = StubTransport(replies) }
  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    if request.url!.path.contains("/evidence/") { return try await gated.send(request) }
    return try await initial.send(request)
  }
}
