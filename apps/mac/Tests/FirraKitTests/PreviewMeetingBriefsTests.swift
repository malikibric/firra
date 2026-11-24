import Foundation
import Testing

@testable import FirraKit

@Suite struct PreviewMeetingBriefsTests {
  @Test func meetingsAreInTheFutureRelativeToNow() async {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let meetings = await PreviewMeetingBriefs(now: { now }).upcoming(workspaceId: "w")
    #expect(meetings.count == 2)
    #expect(meetings.allSatisfy { $0.startsAt > now })
    #expect(Set(meetings.map(\.id)).count == meetings.count)
    #expect(meetings[0].startsAt.timeIntervalSince(now) <= 600)
  }

  @Test func firstPreviewMeetingExpandsTheNotch() async {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let meetings = await PreviewMeetingBriefs(now: { now }).upcoming(workspaceId: "w")
    var state = NotchState()
    state.apply(.meetings(meetings))
    state.apply(.tick(now))
    #expect(state.mode == .meeting(meetings[0]))
  }
}
