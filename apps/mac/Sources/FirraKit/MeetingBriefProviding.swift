import Foundation

public struct MeetingBriefSummary: Sendable, Equatable, Identifiable {
  public let id: String
  public let clientId: String
  public let clientName: String
  public let startsAt: Date
  public let questions: [String]

  public init(id: String, clientId: String, clientName: String, startsAt: Date, questions: [String]) {
    self.id = id
    self.clientId = clientId
    self.clientName = clientName
    self.startsAt = startsAt
    self.questions = questions
  }
}

public protocol MeetingBriefProviding: Sendable {
  func upcoming(workspaceId: String) async -> [MeetingBriefSummary]
}

public struct NoMeetingBriefs: MeetingBriefProviding {
  public init() {}
  public func upcoming(workspaceId: String) async -> [MeetingBriefSummary] { [] }
}
