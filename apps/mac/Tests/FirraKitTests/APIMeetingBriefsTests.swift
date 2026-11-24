import Foundation
import Testing

@testable import FirraKit

@Suite struct APIMeetingBriefsTests {
  func provider(_ replies: [StubTransport.Reply]) -> (APIMeetingBriefs, StubTransport) {
    let stub = StubTransport(replies)
    let api = APIClient(base: URL(string: "https://api.example")!, transport: stub, tokenProvider: { "tok" })
    return (APIMeetingBriefs(api: api), stub)
  }

  static let body = """
    {"generated_at":"2026-10-03T12:00:00.000Z","items":[
     {"id":"b1","calendar_event_id":"e1","client_id":"c1","client_name":"Redfern","policy":"full",
      "starts_at":"2026-10-03T14:00:00.000Z","basis":"metadata",
      "since_last_meeting":{"records":3,"last_held_at":null},"open_commitments":[],"unresolved_complaints":[],
      "stakeholder_changes":[],"renewal":null,"unknowns":[],
      "questions":[{"text":"Who signs off the roadmap now?","basis":{"kind":"unknown","ref":"no_decision_maker"}}]},
     {"id":"b2","calendar_event_id":"e2","client_id":"c2","client_name":"Bad date","policy":"full",
      "starts_at":"not a date","basis":"metadata","questions":[]}]}
    """

  @Test func mapsBriefsAndSkipsOnesWithoutAReadableStart() async throws {
    let (briefs, stub) = provider([.init(status: 200, body: Self.body)])
    let result = await briefs.upcoming(workspaceId: "w1")
    #expect(result.count == 1)
    #expect(result[0].id == "b1")
    #expect(result[0].clientName == "Redfern")
    #expect(result[0].questions == ["Who signs off the roadmap now?"])
    #expect(result[0].startsAt == Date(timeIntervalSince1970: 1_791_036_000))
    #expect(try #require(stub.requests.first).url?.path == "/v1/workspaces/w1/meetings/upcoming")
  }

  @Test func aFailedRefreshKeepsTheLastGoodAnswerPerWorkspace() async {
    let (briefs, _) = provider([
      .init(status: 200, body: Self.body), .init(status: 500, body: "{}"), .init(status: 500, body: "{}"),
    ])
    #expect(await briefs.upcoming(workspaceId: "w1").count == 1)
    #expect(await briefs.upcoming(workspaceId: "w1").count == 1)
    #expect(await briefs.upcoming(workspaceId: "w2").isEmpty)
  }

  @Test func parsesTimestampsWithAndWithoutFractionalSeconds() {
    #expect(APIMeetingBriefs.parse("2026-10-03T14:00:00.000Z") == APIMeetingBriefs.parse("2026-10-03T14:00:00Z"))
    #expect(APIMeetingBriefs.parse("2026-10-03T16:00:00+02:00") == APIMeetingBriefs.parse("2026-10-03T14:00:00Z"))
    #expect(APIMeetingBriefs.parse("tomorrow") == nil)
  }
}
