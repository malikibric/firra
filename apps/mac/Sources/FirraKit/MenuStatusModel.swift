import Foundation
import Observation

@MainActor @Observable public final class NotificationFocus {
  public var clientIDs: Set<String> = []
  public var decisionIDs: Set<String> = []
  public var actionKinds: Set<String> = []
  public init() {}
}

@MainActor @Observable public final class MenuStatusModel {
  public enum Health { case starting, stopped, running }
  public enum Action: String, Identifiable { case google, claude; public var id: String { rawValue } }
  public struct Notices {
    public var refreshed = false
    public var decisions: [DecisionItem] = []
    public var actions: [Action] = []
  }
  public let focus = NotificationFocus()
  public var health: Health = .starting
  public private(set) var status: LocalStatus?
  public private(set) var attention: [AttentionItem] = []
  public private(set) var ai: LocalAIStatus?
  public private(set) var message: String?
  public private(set) var busy = false
  private let api: APIClient
  private let workspace: String
  private let planner: NotificationPlanner
  private let defaults: UserDefaults
  private let owner: String
  private let clock: () -> Date
  private var lastPoll: Date?
  public init(api: APIClient, workspace: String, owner: String, defaults: UserDefaults = .standard,
              clock: @escaping () -> Date = Date.init) {
    self.api = api; self.workspace = workspace; self.owner = owner
    self.defaults = defaults; self.clock = clock
    planner = NotificationPlanner(store: UserDefaultsShownCardStore(userID: owner, defaults: defaults))
  }
  public static func syncDate(_ value: String?) -> Date? {
    value.flatMap { APIMeetingBriefs.parse($0) }
  }
  public var connections: [LocalStatus.Connection] {
    (status?.connections ?? []).filter { ["gmail", "google_calendar"].contains($0.provider) }
  }
  public var reconnectConnection: LocalStatus.Connection? {
    connections.first { $0.state == "needs_reauth" || $0.state == "error" || ["token_revoked", "invalid_grant", "token_expired"].contains($0.errorClass ?? "") }
  }
  public var actions: [Action] {
    var result: [Action] = []
    if reconnectConnection != nil { result.append(.google) }
    if ["needs_signin", "tool_missing", "not_signed_in", "not_installed"].contains(ai?.state ?? "") {
      result.append(.claude)
    }
    return result
  }
  public var title: String {
    switch health {
    case .starting: return "Firra is starting"
    case .stopped: return "Firra stopped"
    case .running:
      if !actions.isEmpty { return "Firra needs you" }
      if !attention.isEmpty { return attention.count == 1 ? "1 client needs attention" : "\(attention.count) clients need attention" }
      if connections.contains(where: { $0.state == "syncing" }) { return "Syncing" }
      return "Firra"
    }
  }
  public var icon: String {
    switch health {
    case .starting: return "clock"
    case .stopped: return "exclamationmark.triangle"
    case .running:
      if !actions.isEmpty { return "exclamationmark.circle" }
      if !attention.isEmpty { return "exclamationmark.bubble" }
      if connections.contains(where: { $0.state == "syncing" }) { return "arrow.triangle.2.circlepath" }
      return "circle.hexagongrid"
    }
  }
  public var waiting: String? {
    guard let ai else { return nil }
    let count = ai.queue.pending
    return ai.executor == "conversation_only" || ai.state == "conversation_only"
      ? "\(count) emails waiting: ask your AI to process mail"
      : "\(count) emails waiting for your AI"
  }
  public func pollIfDue(active: Bool) async -> Notices {
    guard !busy, lastPoll == nil || clock().timeIntervalSince(lastPoll!) >= (active ? 15 : 60) else { return Notices() }
    return await refresh(active: active)
  }
  public func refresh(active: Bool) async -> Notices {
    guard !busy else { return Notices() }
    busy = true; lastPoll = clock(); defer { busy = false }
    var notices = Notices()
    notices.refreshed = true
    do {
      let result = try await api.localStatus()
      status = result
      health = result.database == "ready" ? .running : result.database == "error" ? .stopped : .starting
      ai = result.ai
      if ai == nil { ai = try await api.aiStatus() }
      guard health == .running else { return notices }
      for action in actions where !active || !focus.actionKinds.contains(action.rawValue) {
        let key = "notifications.action.\(owner).\(action.rawValue)"
        let today = Calendar.current.startOfDay(for: clock())
        if let last = defaults.object(forKey: key) as? Date, last >= today { continue }
        defaults.set(clock(), forKey: key); notices.actions.append(action)
      }
    } catch { health = .stopped; message = "The local engine could not be reached."; return notices }
    do { attention = try await api.attention(workspaceId: workspace).items; message = nil }
    catch { message = "Attention could not be refreshed. Try again." }
    do {
      if case .decisions(let cards) = try await NotificationRoute.load(api: api, workspaceId: workspace) {
        notices.decisions = planner.plan(cards: cards,
          focusedClientIDs: active ? focus.clientIDs : [], focusedDecisionIDs: active ? focus.decisionIDs : [])
      }
    } catch { /* Failed reads never create a notification. */ }
    return notices
  }
  public func syncNow() async {
    do {
      for connection in connections where connection.state != "needs_reauth" {
        try await api.syncConnection(connection.id)
      }
      message = "Sync requested."
    } catch { message = "Sync could not be started. Try again." }
  }
}
