import Foundation
import Observation

@MainActor @Observable
public final class ChangesModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var items: [ChangeItem] = []
  public private(set) var windowDays = 7
  public private(set) var workspaceId: String?

  public struct DayGroup: Identifiable {
    public let day: String
    public let items: [ChangeItem]
    public var id: String { day }
  }

  public var groupedByDay: [DayGroup] {
    var groups: [DayGroup] = []
    for item in items {
      if let index = groups.firstIndex(where: { $0.day == item.asOf }) {
        groups[index] = DayGroup(day: item.asOf, items: groups[index].items + [item])
      } else {
        groups.append(DayGroup(day: item.asOf, items: [item]))
      }
    }
    return groups
  }

  private let api: APIClient
  private let now: @Sendable () -> Date
  private var request = UUID()

  public init(api: APIClient, now: @escaping @Sendable () -> Date = { Date() }) {
    self.api = api
    self.now = now
  }

  public func setWindowDays(_ days: Int) async {
    guard [7, 14, 30].contains(days), days != windowDays else { return }
    windowDays = days
    if let workspaceId { await load(workspaceId: workspaceId) }
  }

  public func load(workspaceId: String) async {
    if self.workspaceId != workspaceId {
      items = []
      self.workspaceId = workspaceId
    }
    let request = UUID()
    self.request = request
    phase = .loading
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(secondsFromGMT: 0)!
    let start = calendar.date(byAdding: .day, value: -windowDays, to: now())!
    let formatter = DateFormatter()
    formatter.calendar = calendar
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = calendar.timeZone
    formatter.dateFormat = "yyyy-MM-dd"
    do {
      let response = try await api.changes(workspaceId: workspaceId, since: formatter.string(from: start))
      guard self.request == request else { return }
      items = response.items
      phase = .ready
    } catch {
      guard self.request == request else { return }
      phase = .failed((error as? APIError)?.userMessage ?? "Could not load changes.")
    }
  }
}
