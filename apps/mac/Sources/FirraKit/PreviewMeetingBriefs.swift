import Foundation

/// Two made-up meetings relative to `now`, for previews and the debug `--preview-meetings` launch
/// argument. Never used in release builds and never reads anything from the server.
public struct PreviewMeetingBriefs: MeetingBriefProviding {
  private let now: @Sendable () -> Date

  public init(now: @escaping @Sendable () -> Date = { Date() }) { self.now = now }

  public func upcoming(workspaceId: String) async -> [MeetingBriefSummary] {
    let start = now()
    return [
      MeetingBriefSummary(
        id: "preview-1", clientId: "preview-client-1", clientName: "Kestrel Labs (preview)",
        startsAt: start.addingTimeInterval(9 * 60),
        questions: [
          "Who owns the reporting deadline you promised last week?",
          "Is the renewal still on the table, or is budget the problem?",
          "Who on your side is now signing off on the roadmap?",
        ]),
      MeetingBriefSummary(
        id: "preview-2", clientId: "preview-client-2", clientName: "Vantage Co (preview)",
        startsAt: start.addingTimeInterval(3 * 3600), questions: []),
    ]
  }
}
