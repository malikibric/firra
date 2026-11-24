import Foundation

/// Meeting briefs from the Firra API. A failed refresh keeps the last good answer for that workspace,
/// so a network blip does not collapse a brief that is already on screen.
public actor APIMeetingBriefs: MeetingBriefProviding {
  private let api: APIClient
  private var lastGood: [String: [MeetingBriefSummary]] = [:]

  public init(api: APIClient) { self.api = api }

  public func upcoming(workspaceId: String) async -> [MeetingBriefSummary] {
    guard let response = try? await api.upcomingMeetings(workspaceId: workspaceId) else {
      return lastGood[workspaceId] ?? []
    }
    let summaries = response.items.compactMap { item -> MeetingBriefSummary? in
      guard let start = Self.parse(item.startsAt) else { return nil }
      return MeetingBriefSummary(
        id: item.id, clientId: item.clientId, clientName: item.clientName, startsAt: start,
        questions: item.questions.map(\.text))
    }
    lastGood[workspaceId] = summaries
    return summaries
  }

  static func parse(_ value: String) -> Date? {
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = fractional.date(from: value) { return date }
    let plain = ISO8601DateFormatter()
    plain.formatOptions = [.withInternetDateTime]
    return plain.date(from: value)
  }
}
