import Foundation
import Testing
@testable import FirraKit

func notchItem(_ id: String, _ risk: String, name: String? = nil) -> AttentionItem {
  AttentionItem(clientId: id, name: name ?? id, status: "active", policy: "full", asOf: "2026-10-03",
    riskState: risk, previousRiskState: nil, stateSince: "2026-10-01", expansionState: "none",
    strength: "weak", play: nil, daysToNotice: nil, drivers: [])
}

let notchNow = Date(timeIntervalSince1970: 10_000)
func notchMeeting(_ id: String = "m", in seconds: TimeInterval = 600) -> MeetingBriefSummary {
  MeetingBriefSummary(id: id, clientId: "c", clientName: "Client", startsAt: notchNow.addingTimeInterval(seconds),
    questions: ["One?", "Two?", "Three?", "Four?"])
}

@Suite struct NotchStateTests {
  @Test func ranksCountsAndStableTies() {
    var state = NotchState()
    #expect(state.level == .calm && state.mode == .dot && !state.meetingReady)
    #expect(state.headline == "Nothing needs attention" && state.topItems.isEmpty)
    state.apply(.items([notchItem("z", "watch"), notchItem("b", "high"),
      notchItem("a1", "high", name: "A"), notchItem("a2", "high", name: "A"),
      notchItem("e", "elevated"), notchItem("u", "unexpected")]))
    #expect(state.level == .high && state.headline == "4 clients need attention")
    #expect(state.topItems.map(\.id) == ["a1", "a2", "b"])
    state.apply(.items([notchItem("e", "elevated"), notchItem("w", "watch")]))
    #expect(state.level == .elevated && state.headline == "1 client needs attention")
    state.apply(.items([notchItem("w", "watch")]))
    #expect(state.level == .watch && state.headline == "Nothing needs attention")
    state.apply(.items([notchItem("u", "HIGH"), notchItem("s", "stable")]))
    #expect(state.level == .calm && state.topItems.isEmpty)
  }

  @Test func pointerAndClicks() {
    var state = NotchState()
    state.apply(.hover(true)); #expect(state.mode == .hint)
    state.apply(.hover(false)); #expect(state.mode == .dot)
    for entry in [NotchEvent.click, .hover(true)] {
      state.apply(entry)
      if state.mode == .hint { state.apply(.click) }
      #expect(state.mode == .list)
      state.apply(.hover(false)); state.apply(.hover(true)); #expect(state.mode == .list)
      state.apply(.click); #expect(state.mode == .dot)
    }
    state.apply(.click); state.apply(.dismiss); #expect(state.mode == .dot)
  }

  @Test func earliestMeetingBadgeAndStartBoundary() {
    var state = NotchState()
    let first = notchMeeting("first", in: 300), later = notchMeeting("later")
    state.apply(.meetings([later, notchMeeting("past", in: -1), first]))
    state.apply(.tick(notchNow))
    #expect(state.mode == .meeting(first) && state.meetingReady)
    #expect(first.questions.count == 4)
    state.apply(.hover(true)); state.apply(.hover(false)); #expect(state.mode == .meeting(first))
    state.apply(.tick(first.startsAt)); #expect(state.mode == .dot && state.meetingReady)
    state.apply(.tick(first.startsAt)); #expect(state.mode == .meeting(later))
    state.apply(.tick(later.startsAt)); #expect(state.mode == .dot && !state.meetingReady)
  }

  @Test func leadWindowAndListAreRespected() {
    var state = NotchState(autoExpandLead: 60)
    let meeting = notchMeeting(in: 61)
    state.apply(.meetings([meeting])); state.apply(.tick(notchNow))
    #expect(state.mode == .dot && state.meetingReady)
    state.apply(.click); state.apply(.tick(notchNow.addingTimeInterval(1)))
    #expect(state.mode == .list)
    state.apply(.dismiss); state.apply(.hover(true)); state.apply(.tick(notchNow.addingTimeInterval(1)))
    #expect(state.mode == .meeting(meeting))
  }

  @Test(arguments: [NotchEvent.click, .dismiss, .meetings([])])
  func shownMeetingNeverExpandsAgain(_ close: NotchEvent) {
    var state = NotchState()
    let meeting = notchMeeting()
    state.apply(.meetings([meeting])); state.apply(.tick(notchNow)); state.apply(close)
    #expect(state.mode == .dot)
    state.apply(.meetings([meeting])); state.apply(.hover(true)); state.apply(.tick(notchNow))
    #expect(state.mode == .hint && state.meetingReady)
    state.apply(.click); state.apply(.dismiss); state.apply(.tick(notchNow))
    #expect(state.mode == .dot)
    state.apply(.meetings([])); #expect(!state.meetingReady)
  }

  @Test func emptyItemsCollapseExceptMeeting() {
    var state = NotchState()
    for entry in [NotchEvent.hover(true), .click] {
      state.apply(.items([notchItem("c", "high")]))
      state.apply(entry); state.apply(.items([]))
      #expect(state.mode == .dot && state.level == .calm && state.topItems.isEmpty)
    }
    let meeting = notchMeeting()
    state.apply(.meetings([meeting])); state.apply(.tick(notchNow)); state.apply(.items([]))
    #expect(state.mode == .meeting(meeting) && state.headline == "Nothing needs attention")
  }
}
