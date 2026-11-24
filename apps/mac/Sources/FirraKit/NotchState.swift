import Foundation

public enum NotchLevel: Int, Sendable { case calm = 0, watch, elevated, high }
public enum NotchMode: Sendable, Equatable { case dot, hint, list, meeting(MeetingBriefSummary) }

public enum NotchEvent: Sendable {
  case hover(Bool), click, dismiss
  case items([AttentionItem]), meetings([MeetingBriefSummary])
  case tick(Date)
}

/// Presentation state only: risk ranks come from the existing attention rules.
public struct NotchState: Sendable, Equatable {
  public private(set) var level: NotchLevel = .calm
  public private(set) var mode: NotchMode = .dot
  public private(set) var headline = "Nothing needs attention"
  public private(set) var topItems: [AttentionItem] = []
  public private(set) var meetingReady = false
  private let autoExpandLead: TimeInterval
  private var meetings: [MeetingBriefSummary] = []
  private var shown: Set<String> = []
  private var lastTick: Date?

  public init(autoExpandLead: TimeInterval = 600) { self.autoExpandLead = autoExpandLead }

  public mutating func apply(_ event: NotchEvent) {
    switch event {
    case .hover(let inside):
      if mode == .dot && inside { mode = .hint }
      else if mode == .hint && !inside { mode = .dot }
    case .click:
      switch mode {
      case .dot, .hint: mode = .list
      case .list, .meeting: mode = .dot
      }
    case .dismiss:
      if case .meeting(let meeting) = mode { shown.insert(meeting.id) }
      mode = .dot
    case .items(let items):
      let rank = items.map { AttentionChanges.rank($0.riskState) }.max() ?? 0
      level = NotchLevel(rawValue: rank) ?? .calm
      topItems = items.enumerated().filter { AttentionChanges.rank($0.element.riskState) >= 1 }
        .sorted {
          let left = AttentionChanges.rank($0.element.riskState)
          let right = AttentionChanges.rank($1.element.riskState)
          if left != right { return left > right }
          if $0.element.name != $1.element.name { return $0.element.name < $1.element.name }
          return $0.offset < $1.offset
        }.prefix(3).map(\.element)
      let count = items.filter { AttentionChanges.rank($0.riskState) >= 2 }.count
      headline = count == 0 ? "Nothing needs attention"
        : count == 1 ? "1 client needs attention" : "\(count) clients need attention"
      if items.isEmpty {
        switch mode {
        case .meeting: break
        default: mode = .dot
        }
      }
    case .meetings(let upcoming):
      meetings = upcoming
      if case .meeting(let meeting) = mode, !upcoming.contains(where: { $0.id == meeting.id }) {
        mode = .dot
      }
      updateMeetingReady()
    case .tick(let now):
      lastTick = now
      updateMeetingReady()
      if case .meeting(let meeting) = mode {
        if now >= meeting.startsAt { mode = .dot }
      } else if mode == .dot || mode == .hint {
        let next = meetings.filter {
          !shown.contains($0.id) && $0.startsAt > now
            && $0.startsAt.timeIntervalSince(now) <= autoExpandLead
        }.sorted { $0.startsAt < $1.startsAt }
        if let meeting = next.first {
          shown.insert(meeting.id)
          mode = .meeting(meeting)
        }
      }
    }
  }

  private mutating func updateMeetingReady() {
    meetingReady = lastTick.map { now in meetings.contains { $0.startsAt > now } } ?? false
  }
}
