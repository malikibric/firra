import Foundation
import Testing
@testable import FirraKit

private let clientsJSON = #"""
{"items":[{"id":"c1","name":"Acme","status":"active","policy":"metadata_only","domains":["acme.com"],"created_at":"2026-10-01T09:00:00Z"}]}
"""#

private func api(_ replies: [StubTransport.Reply]) -> (APIClient, StubTransport) {
  let transport = StubTransport(replies)
  return (APIClient(base: URL(string: "https://api.example")!, transport: transport, tokenProvider: { "tok" }), transport)
}

private func json(_ request: URLRequest) -> [String: Any] {
  (try? JSONSerialization.jsonObject(with: request.httpBody ?? Data()) as? [String: Any]) ?? [:]
}

@MainActor @Suite struct ClientsModelTests {
  @Test func parsesDomainsFromFreeText() {
    #expect(ClientsModel.parseDomains(" Acme.com, b.co.uk;\nc.io ,, ") == ["acme.com", "b.co.uk", "c.io"])
    #expect(ClientsModel.parseDomains("").isEmpty)
  }

  @Test func decodesPoliciesAndReloadsAfterAWrite() async {
    let (client, transport) = api([
      .init(status: 200, body: clientsJSON),
      .init(status: 200, body: #"{"id":"c2"}"#),
      .init(status: 200, body: clientsJSON),
    ])
    let model = ClientsModel(api: client)
    await model.load(workspaceId: "w")
    #expect(model.clients.first?.policy == .metadataOnly && model.phase == .ready)
    let added = await model.add(workspaceId: "w", name: " Beta ", domains: "Beta.com", policy: .full)
    #expect(added)
    let body = json(transport.requests[1])
    #expect(body["name"] as? String == "Beta" && body["domains"] as? [String] == ["beta.com"])
    #expect(body["policy"] as? String == "full" && body["confirm_exclusion"] as? Bool == false)
    #expect(transport.requests[2].httpMethod == "GET")
  }

  @Test func excludingSendsTheConfirmationTheServerRequires() async {
    let (client, transport) = api([.init(status: 204, body: ""), .init(status: 200, body: clientsJSON)])
    let model = ClientsModel(api: client)
    #expect(await model.setPolicy(workspaceId: "w", clientId: "c1", policy: .excluded))
    let request = transport.requests[0]
    #expect(request.httpMethod == "PUT" && request.url!.path == "/v1/workspaces/w/clients/c1/policy")
    #expect(json(request)["policy"] as? String == "excluded" && json(request)["confirm_exclusion"] as? Bool == true)
  }

  @Test func aRefusedWriteShowsTheServerMessageAndKeepsTheList() async {
    let (client, _) = api([
      .init(status: 200, body: clientsJSON),
      .init(status: 422, body: #"{"error":"invalid_request","message":"Invalid domain.","request_id":"r"}"#),
    ])
    let model = ClientsModel(api: client)
    await model.load(workspaceId: "w")
    #expect(await model.setDomains(workspaceId: "w", clientId: "c1", domains: "nope") == false)
    #expect(model.message != nil && model.clients.count == 1)
  }
}

private let servicesJSON = #"""
{"items":[{"id":"s1","name":"SEO","description":"Search","keywords":["seo"],"created_at":"2026-10-01T09:00:00Z"}]}
"""#

@MainActor @Suite struct ServicesModelTests {
  @Test func parsesKeywordsFromFreeText() {
    #expect(ServicesModel.parseKeywords(" SEO, paid ads\nseo ,, ") == ["seo", "paid ads"])
    #expect(ServicesModel.parseKeywords("").isEmpty)
  }

  @Test func createPostsNameDescriptionAndKeywordsThenReloads() async {
    let (client, transport) = api([
      .init(status: 200, body: servicesJSON),
      .init(status: 200, body: #"{"id":"s2"}"#),
      .init(status: 200, body: servicesJSON),
    ])
    let model = ServicesModel(api: client)
    await model.load(workspaceId: "w")
    #expect(model.services.first?.name == "SEO" && model.phase == .ready)
    #expect(await model.save(workspaceId: "w", serviceId: nil, name: " Ads ", description: "Paid", keywords: "PPC, ppc"))
    let request = transport.requests[1]
    #expect(request.httpMethod == "POST" && request.url!.path == "/v1/workspaces/w/services")
    #expect(json(request)["name"] as? String == "Ads")
    #expect(json(request)["description"] as? String == "Paid")
    #expect(json(request)["keywords"] as? [String] == ["ppc"])
    #expect(transport.requests[2].httpMethod == "GET")
  }

  @Test func saveExistingPutsToTheServicePath() async {
    let (client, transport) = api([.init(status: 200, body: #"{"id":"s1"}"#), .init(status: 200, body: servicesJSON)])
    let model = ServicesModel(api: client)
    #expect(await model.save(workspaceId: "w", serviceId: "s1", name: "SEO", description: "", keywords: ""))
    let request = transport.requests[0]
    #expect(request.httpMethod == "PUT" && request.url!.path == "/v1/workspaces/w/services/s1")
  }

  @Test func deleteCallsDeleteThenReloads() async {
    let (client, transport) = api([.init(status: 204, body: ""), .init(status: 200, body: servicesJSON)])
    let model = ServicesModel(api: client)
    #expect(await model.delete(workspaceId: "w", serviceId: "s1"))
    #expect(transport.requests[0].httpMethod == "DELETE")
    #expect(transport.requests[0].url!.path == "/v1/workspaces/w/services/s1")
    #expect(model.services.count == 1)
  }

  @Test func aRefusedWriteShowsTheServerMessageAndKeepsTheList() async {
    let (client, _) = api([
      .init(status: 200, body: servicesJSON),
      .init(status: 422, body: #"{"error":"invalid_request","message":"Duplicate name.","request_id":"r"}"#),
    ])
    let model = ServicesModel(api: client)
    await model.load(workspaceId: "w")
    #expect(await model.save(workspaceId: "w", serviceId: nil, name: "SEO", description: "", keywords: "") == false)
    #expect(model.message == "Duplicate name." && model.services.count == 1)
  }
}

@MainActor @Suite struct AuditAndReplayTests {
  let page1 = #"{"items":[{"id":"a1","action":"read.attention","actor_type":"user","actor_id":"12345678-aaaa","target_type":null,"target_id":null,"created_at":"2026-10-02T09:00:00Z"}],"next_page":2}"#
  let page2 = #"{"items":[{"id":"a2","action":"client.policy_changed","actor_type":"user","actor_id":null,"target_type":"client","target_id":null,"created_at":"2026-10-01T09:00:00Z"}],"next_page":null}"#

  @Test func loadsAuditPagesInOrder() async {
    let (client, transport) = api([.init(status: 200, body: page1), .init(status: 200, body: page2)])
    let model = AuditModel(api: client)
    await model.load(workspaceId: "w")
    #expect(model.entries.count == 1 && model.nextPage == 2 && model.phase == .ready)
    await model.loadMore(workspaceId: "w")
    #expect(model.entries.map(\.id) == ["a1", "a2"] && model.nextPage == nil)
    #expect(transport.requests[1].url!.query == "page=2")
  }

  @Test func replayRequestsTheWindowAsUTCDates() async {
    let (client, transport) = api([.init(status: 200, body: #"{"id":"r1"}"#)])
    let model = ReplayModel(api: client)
    let start = Date(timeIntervalSince1970: 1_767_225_600)  // 2026-01-01 00:00 UTC
    await model.request(workspaceId: "w", start: start, end: start.addingTimeInterval(86400 * 30), stepDays: 7)
    #expect(model.state == .requested)
    let body = json(transport.requests[0])
    #expect(body["window_start"] as? String == "2026-01-01" && body["window_end"] as? String == "2026-01-31")
    #expect(body["step_days"] as? Int == 7)
  }

  @Test func replayRefusesAnEndBeforeTheStart() async {
    let (client, transport) = api([])
    let model = ReplayModel(api: client)
    let now = Date()
    await model.request(workspaceId: "w", start: now, end: now.addingTimeInterval(-86400), stepDays: 7)
    if case .failed = model.state {} else { Issue.record("expected failure") }
    #expect(transport.requests.isEmpty)
  }
}

@MainActor @Suite struct ClientDetailTests {
  let timeline = #"{"items":[{"id":"state:x","occurred_at":"2026-09-20T00:00:00Z","client_id":"c1","kind":"state_change","from":"elevated","to":"high"}],"next_cursor":null}"#
  let detail = #"""
  {"generated_at":"2026-10-02T09:00:00Z","client":{"id":"c1","name":"Redfern","status":"active","policy":"full","mrr":4200,"currency":"EUR",
  "renewal_date":"2026-12-01","notice_period_days":60,
  "assessment":{"risk_state":"high","state_since":"2026-09-20","expansion_state":"none","play":"fix_first","as_of":"2026-10-01",
  "previous_risk_state":"elevated","strength":"strong","caveats":[{"code":"thin_history","detail":"Only 6 weeks of history."}],
  "drivers":[{"type":"repeated_complaint","family":"risk","severity":"high","occurred_at":null,"primary":true,"evidence_ids":["e2","e1","gone"],"basis":"evidence"}]},
  "state_history":[{"as_of":"2026-09-20","from":"elevated","to":"high"}],
  "stakeholders":[{"display_name":"Ana","role_title":"CMO","is_decision_maker":true,"last_seen_at":null}],
  "commitments":[{"owner_side":"firm","text":"Send the audit","due_date":null,"status":"late"}],
  "goals":[],"needs":[],"outcomes":[]}}
  """#
  let evidence = #"""
  {"items":[{"id":"e1","quote":"this is the third delay","locator":{},"deep_link":"https://mail.example/1","occurred_at":"2026-09-18T10:00:00Z",
  "author_name":"Ana","author_role":"CMO","subject":"Report","source_ref":"abcdefgh","client_id":"c1"},
  {"id":"e2","quote":"we are losing patience","locator":{},"deep_link":null,"occurred_at":"2026-09-19T10:00:00Z",
  "author_name":null,"author_role":null,"subject":null,"source_ref":"abcdefgh","client_id":"c1"}],"next_cursor":null}
  """#

  @Test func loadsTheDetailAndMapsDriversToTheirQuotesInOrder() async {
    let (client, transport) = api([
      .init(status: 200, body: detail), .init(status: 200, body: timeline), .init(status: 200, body: evidence),
    ])
    let model = ClientDetailModel(api: client)
    await model.load(workspaceId: "w", clientId: "c1")
    #expect(model.detail?.assessment?.drivers.count == 1 && model.phase == .ready)
    #expect(model.timeline.first?.summary == "Risk elevated → high")
    let quotes = model.quotes(for: model.detail!.assessment!.drivers[0])
    #expect(quotes.map(\.id) == ["e2", "e1"])  // evidence ids order, unknown id skipped
    #expect(transport.requests[0].url!.path == "/v1/workspaces/w/clients/c1")
    #expect(transport.requests[1].url!.path == "/v1/workspaces/w/clients/c1/timeline")
    #expect(transport.requests[2].url!.path == "/v1/workspaces/w/clients/c1/evidence")
  }

  @Test func theFactsStayWhenTheQuotesCannotBeLoaded() async {
    let (client, _) = api([
      .init(status: 200, body: detail), .init(status: 500, body: "{}"), .init(status: 500, body: "{}"),
    ])
    let model = ClientDetailModel(api: client)
    await model.load(workspaceId: "w", clientId: "c1")
    #expect(model.phase == .ready && model.detail?.commitments.first?.status == "late" && model.timeline.isEmpty)
    #expect(model.quotes(for: model.detail!.assessment!.drivers[0]).isEmpty)
  }

  @Test func aMissingClientIsAnErrorNotAnEmptyScreen() async {
    let (client, _) = api([.init(status: 404, body: #"{"error":"not_found","message":"Not found.","request_id":"r"}"#)])
    let model = ClientDetailModel(api: client)
    await model.load(workspaceId: "w", clientId: "c1")
    if case .failed = model.phase {} else { Issue.record("expected failure") }
  }

  @Test func recordsAnOutcomeThenReloads() async {
    let (client, transport) = api([
      .init(status: 200, body: #"{"id":"o1"}"#), .init(status: 200, body: detail), .init(status: 200, body: timeline),
      .init(status: 200, body: evidence),
    ])
    let model = ClientDetailModel(api: client)
    await model.recordOutcome(
      workspaceId: "w", clientId: "c1", event: .priceIncrease, on: Date(timeIntervalSince1970: 1_767_225_600))
    let body = json(transport.requests[0])
    #expect(transport.requests[0].url!.path == "/v1/workspaces/w/clients/c1/outcomes")
    #expect(body["event"] as? String == "price_increase" && body["occurred_at"] as? String == "2026-01-01")
    #expect(model.detail != nil)
  }
}

@MainActor @Suite struct DecisionsModelTests {
  let page = #"""
  {"items":[{"id":"d1","client_id":"c1","client_name":"Redfern","week":"2026-09-28","kind":"risk","title":"Call Ana",
  "body":"Three delays in a month.","play":"fix_first","strength":"strong","priority":1,"status":"new",
  "created_at":"2026-09-28T08:00:00Z","evidence_ids":["e1","e2"],"feedback":null}],"next_cursor":null}
  """#

  @Test func listsNewCardsVerifiesTheirQuotesAndSendsTheVerdict() async {
    let (client, transport) = api([
      .init(status: 200, body: page), .init(status: 200, body: #"{"id":"e1","quote":"this is the third delay","locator":{},"deep_link":null,"occurred_at":"2026-09-18T10:00:00Z","author_name":null,"author_role":null,"subject":null,"source_ref":"abcdefgh","client_id":"c1"}"#), .init(status: 200, body: #"{"id":"e2","quote":"this is the third delay","locator":{},"deep_link":null,"occurred_at":"2026-09-18T10:00:00Z","author_name":null,"author_role":null,"subject":null,"source_ref":"abcdefgh","client_id":"c1"}"#),
      .init(status: 200, body: #"{"id":"f1"}"#), .init(status: 200, body: page),
    ])
    let model = DecisionsModel(api: client)
    await model.load(workspaceId: "w")
    #expect(model.cards.first?.title == "Call Ana" && transport.requests[0].url!.query?.contains("status=new") == true)
    #expect(model.isVerified(model.cards[0]) && model.quotes["d1"]?.count == 2)
    await model.rate(workspaceId: "w", card: model.cards[0], verdict: .alreadyKnown)
    #expect(transport.requests[3].url!.path == "/v1/workspaces/w/decisions/d1/feedback")
    #expect(json(transport.requests[3])["verdict"] as? String == "already_known")
    #expect(transport.requests[4].httpMethod == "GET")
  }

  @Test func aFailedRatingShowsAMessageAndKeepsTheCards() async {
    let (client, _) = api([
      .init(status: 200, body: page), .init(status: 404, body: #"{"error":"not_found","message":"Not found.","request_id":"r"}"#),
    ])
    let model = DecisionsModel(api: client)
    await model.load(workspaceId: "w")
    await model.rate(workspaceId: "w", card: model.cards[0], verdict: .useful)
    #expect(model.message != nil && model.cards.count == 1)
  }
}

@MainActor @Suite struct ReviewFixTests {
  @Test func restrictingAClientTellsTheAppToRemoveItsLocalContent() async {
    let (client, _) = api([.init(status: 204, body: ""), .init(status: 200, body: clientsJSON)])
    let model = ClientsModel(api: client)
    var restricted: [String] = []
    model.onClientRestricted = { restricted.append($0) }
    #expect(await model.setPolicy(workspaceId: "w", clientId: "c1", policy: .metadataOnly))
    #expect(restricted == ["c1"])
  }

  @Test func makingAClientFullAgainRemovesNothing() async {
    let (client, _) = api([.init(status: 204, body: ""), .init(status: 200, body: clientsJSON)])
    let model = ClientsModel(api: client)
    var restricted: [String] = []
    model.onClientRestricted = { restricted.append($0) }
    _ = await model.setPolicy(workspaceId: "w", clientId: "c1", policy: .full)
    #expect(restricted.isEmpty)
  }

  @Test func aFailedPolicyChangeRemovesNothing() async {
    let (client, _) = api([.init(status: 500, body: "{}")])
    let model = ClientsModel(api: client)
    var restricted: [String] = []
    model.onClientRestricted = { restricted.append($0) }
    #expect(await model.setPolicy(workspaceId: "w", clientId: "c1", policy: .excluded) == false)
    #expect(restricted.isEmpty)
  }

  @Test func anEvidenceDriverWithoutLoadableQuotesIsNotShownAsAFinding() async {
    let detail = ClientDetailTests().detail
    let (client, _) = api([
      .init(status: 200, body: detail), .init(status: 500, body: "{}"), .init(status: 500, body: "{}"),
    ])
    let model = ClientDetailModel(api: client)
    await model.load(workspaceId: "w", clientId: "c1")
    #expect(model.evidenceMissing(for: model.detail!.assessment!.drivers[0]))
  }

  @Test func decisionsFollowTheCursorAndDeduplicate() async {
    let first = #"{"items":[{"id":"d1","client_id":"c","client_name":"A","week":"2026-09-28","kind":"risk","title":"t","body":"b","play":"p","strength":"strong","priority":1,"status":"new","created_at":"2026-09-28T08:00:00Z","evidence_ids":[],"feedback":null}],"next_cursor":"abc"}"#
    let second = first.replacingOccurrences(of: #""next_cursor":"abc""#, with: #""next_cursor":null"#)
      .replacingOccurrences(of: #""id":"d1""#, with: #""id":"d2""#)
    let (client, transport) = api([.init(status: 200, body: first), .init(status: 200, body: second)])
    let model = DecisionsModel(api: client)
    await model.load(workspaceId: "w")
    await model.loadMore(workspaceId: "w")
    #expect(model.cards.map(\.id) == ["d1", "d2"] && model.nextCursor == nil)
    #expect(transport.requests[1].url!.query?.contains("cursor=abc") == true)
  }

  @Test func aCardsQuotesLoadOnDemandAndFailureOffersARetry() async {
    let one = #"{"id":"e1","quote":"this is the third delay","locator":{},"deep_link":null,"occurred_at":"2026-09-18T10:00:00Z","author_name":null,"author_role":null,"subject":null,"source_ref":"abcdefgh","client_id":"c1"}"#
    let card = DecisionsModelTests().page
    let (client, transport) = api([
      .init(status: 200, body: card), .init(status: 200, body: one), .init(status: 500, body: "{}"),
    ])
    let model = DecisionsModel(api: client)
    await model.load(workspaceId: "w")  // verifies the card right away
    // e1 loads, e2 fails: the card shows the quote it could verify.
    #expect(model.quotes["d1"]?.count == 1 && !model.quotesFailed.contains("d1"))
    #expect(transport.requests[1].url!.path == "/v1/workspaces/w/evidence/e1")
    // Nothing loads: no claim without evidence, a retry is offered.
    let (failing, _) = api([.init(status: 200, body: card), .init(status: 500, body: "{}"), .init(status: 500, body: "{}")])
    let broken = DecisionsModel(api: failing)
    await broken.load(workspaceId: "w")
    #expect(broken.quotes["d1"] == nil && broken.quotesFailed.contains("d1") && !broken.isVerified(broken.cards[0]))
  }

  @Test func anAuditPageFailureKeepsTheContinuation() async {
    let first = AuditAndReplayTests().page1
    let (client, _) = api([.init(status: 200, body: first), .init(status: 500, body: "{}")])
    let model = AuditModel(api: client)
    await model.load(workspaceId: "w")
    await model.loadMore(workspaceId: "w")
    #expect(model.nextPage == 2 && model.loadMoreFailed && model.entries.count == 1)
  }
}

@MainActor @Suite struct ReplayResultsTests {
  let runs = #"""
  {"generated_at":"2026-10-03T08:00:00Z","items":[{"id":"r1","window_start":"2025-10-01","window_end":"2026-10-01","step_days":7,
  "status":"succeeded","finished_at":"2026-10-02T09:00:00Z","narrated":true,"totals":{"losses":3,"losses_detected_30d":2,
  "median_lead_days":74,"retained":10,"retained_with_elevated":1,"false_alarm_client_weeks_per_10_clients_per_month":0.4,"flicker":0}}]}
  """#
  let clients = #"{"generated_at":"2026-10-03T08:00:00Z","items":[{"id":"c1","name":"Redfern","status":"churned","policy":"full","mrr":null,"currency":null,"renewal_date":null,"notice_period_days":null,"assessment":null}]}"#
  let story = #"""
  {"generated_at":"2026-10-03T08:00:00Z","run_id":"r1","client_id":"c1","version":1,"evidence_ids":[],
   "narrative":{"status":"churned","end_date":"2026-06-01","end_kind":"notice","detection_at":"2026-01-12","detection_state":"elevated",
   "confirmed_at":null,"lead_days":140,"confirmed_lead_days":null,"max_state":"high","weeks_elevated":9,
   "transitions":[{"as_of":"2026-01-12","from_state":"watch","to_state":"elevated","play":"fix_first","strength":"strong",
     "new_drivers":[],"active_drivers":[],"caveats":[],"days_before_end":140,"only_ended":false}],
   "visible_at_detection":[],"detection_timing":[],"detection_play":"fix_first","caveats":["thin_history"],"last_as_of":"2026-06-01",
   "last_state":"high","unconfirmed_at_end":false,"events":[]},
   "weeks":[{"as_of":"2026-01-05","risk":"watch","expansion":"none","play":null,"strength":"weak"},
            {"as_of":"2026-01-12","risk":"elevated","expansion":"none","play":"fix_first","strength":"strong"}]}
  """#

  @Test func listsRunsAndClientsThenOpensOneStory() async {
    let (client, transport) = api([
      .init(status: 200, body: runs), .init(status: 200, body: clients), .init(status: 200, body: story),
    ])
    let model = ReplayModel(api: client)
    await model.loadRuns(workspaceId: "w")
    #expect(model.runs.first?.totals?.losses == 3 && model.clients.first?.name == "Redfern")
    #expect(transport.requests[1].url!.query == "status=all")
    await model.open(workspaceId: "w", runId: "r1", clientId: "c1")
    #expect(model.result?.narrative.leadDays == 140 && model.result?.weeks.count == 2)
    #expect(transport.requests[2].url!.path == "/v1/workspaces/w/replay/runs/r1/clients/c1")
  }

  @Test func aClientOutsideTheWindowSaysSo() async {
    let (client, _) = api([.init(status: 404, body: #"{"error":"not_found","message":"Not found.","request_id":"r"}"#)])
    let model = ReplayModel(api: client)
    await model.open(workspaceId: "w", runId: "r1", clientId: "c9")
    #expect(model.result == nil && model.message?.contains("no story") == true)
  }
}

@Suite struct AttentionChangesTests {
  func item(_ id: String, _ risk: String) -> AttentionItem {
    try! JSONDecoder.api.decode(
      AttentionItem.self,
      from: Data(#"{"client_id":"\#(id)","name":"\#(id)","status":"active","policy":"full","as_of":"2026-10-01","risk_state":"\#(risk)","previous_risk_state":null,"state_since":"2026-09-25","expansion_state":"none","strength":"weak","play":null,"days_to_notice":null,"drivers":[]}"#.utf8))
  }

  @Test func theFirstCheckIsTheBaselineAndNeverNotifies() {
    var changes = AttentionChanges()
    #expect(changes.escalations(in: [item("a", "high")]).isEmpty)
  }

  @Test func onlyRisesIntoElevatedOrHighNotify() {
    var changes = AttentionChanges()
    _ = changes.escalations(in: [item("a", "watch"), item("b", "elevated"), item("c", "high")])
    let next = changes.escalations(in: [
      item("a", "elevated"),  // rose into elevated: notify
      item("b", "elevated"),  // unchanged: no
      item("c", "elevated"),  // fell: no
      item("d", "high"),  // new and high: notify
      item("e", "watch"),  // new but only watch: no
    ])
    #expect(next.map(\.clientId) == ["a", "d"])
  }
}

@MainActor @Suite struct GapFixTests {
  @Test func renamingSendsTheNameAndKeepsTheStatus() async {
    let (client, transport) = api([.init(status: 204, body: ""), .init(status: 200, body: clientsJSON)])
    let model = ClientsModel(api: client)
    let acme = SetupClient(id: "c1", name: "Acme", status: "active", policy: .full, domains: [])
    #expect(await model.rename(workspaceId: "w", client: acme, to: " Acme Corp "))
    #expect(transport.requests[0].httpMethod == "PATCH" && transport.requests[0].url!.path == "/v1/workspaces/w/clients/c1")
    #expect(json(transport.requests[0])["name"] as? String == "Acme Corp" && json(transport.requests[0])["status"] as? String == "active")
    // The same name sends nothing.
    #expect(await model.rename(workspaceId: "w", client: acme, to: "Acme") && transport.requests.count == 2)
  }

  @Test func anotherWorkspaceNeverShowsThePreviousClients() async {
    let item = #"{"client_id":"c","name":"A","status":"active","policy":"full","as_of":"2026-10-01","risk_state":"high","previous_risk_state":null,"state_since":"2026-09-25","expansion_state":"none","strength":"weak","play":null,"days_to_notice":null,"drivers":[]}"#
    let (client, _) = api([
      .init(status: 200, body: #"{"generated_at":"x","items":[\#(item)]}"#), .init(status: 500, body: "{}"),
    ])
    let model = AttentionModel(api: client)
    await model.load(workspaceId: "w1")
    #expect(model.items.count == 1)
    await model.load(workspaceId: "w2")
    #expect(model.items.isEmpty && model.workspaceId == "w2")
  }

  @Test func aFailedOutcomeIsReportedAndNotTakenAsSaved() async {
    let (client, _) = api([.init(status: 422, body: #"{"error":"invalid_request","message":"Bad date.","request_id":"r"}"#)])
    let model = ClientDetailModel(api: client)
    let saved = await model.recordOutcome(workspaceId: "w", clientId: "c1", event: .renewed, on: Date())
    #expect(!saved && model.message != nil)
  }

  @Test func metadataDriversExplainTheirNumbers() throws {
    let driver = try JSONDecoder.api.decode(
      DetailDriver.self,
      from: Data(#"{"type":"reduced_engagement","family":"risk","severity":"medium","occurred_at":null,"primary":true,"evidence_ids":[],"basis":"metadata","inputs":{"messages_30":4,"baseline_per_30":11.5,"person_id":"p","last_message_at":"2026-09-20T10:00:00Z"}}"#.utf8))
    #expect(driver.explanation == [
      "usual per 30 days: 11.5", "last message: 2026-09-20", "messages in the last 30 days: 4",
    ])
  }
}
