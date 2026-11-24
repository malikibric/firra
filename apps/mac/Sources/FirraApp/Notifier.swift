import AppKit
import FirraKit
import Foundation
import UserNotifications

extension Notification.Name {
  static let firraOpenMain = Notification.Name("FirraOpenMain")
}

/// Presents only the engine-important decisions and daily actions selected by the planner.
@MainActor final class Notifier: NSObject, UNUserNotificationCenterDelegate {
  private var asked = false
  private let focus: NotificationFocus
  private let openClient: (String, String) -> Void
  private let openAction: (String) -> Void
  init(focus: NotificationFocus, openClient: @escaping (String, String) -> Void, openAction: @escaping (String) -> Void) {
    self.focus = focus
    self.openClient = openClient; self.openAction = openAction
    super.init()
    UNUserNotificationCenter.current().delegate = self
  }
  func notice(_ notices: MenuStatusModel.Notices) {
    for card in notices.decisions {
      if NSApplication.shared.isActive && (focus.clientIDs.contains(card.clientId) || focus.decisionIDs.contains(card.id)) { continue }
      let content = UNMutableNotificationContent()
      content.title = "\(card.clientName) needs attention"
      content.body = card.title
      content.userInfo = ["client_id": card.clientId, "client_name": card.clientName, "decision_id": card.id]
      UNUserNotificationCenter.current().add(UNNotificationRequest(
        identifier: "decision-\(card.id)", content: content, trigger: nil))
    }
    for action in notices.actions {
      if NSApplication.shared.isActive && focus.actionKinds.contains(action.rawValue) { continue }
      let content = UNMutableNotificationContent()
      content.title = "Firra needs you"
      content.body = action == .google ? "Google needs to be reconnected" : "Sign in to Claude Code"
      content.userInfo = ["action": action.rawValue]
      UNUserNotificationCenter.current().add(UNNotificationRequest(
        identifier: "engine-action-\(action.rawValue)", content: content, trigger: nil))
    }
  }
  func requestPermissionOnce() {
    guard !asked else { return }; asked = true
    UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
  }
  nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
    willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
    let info = notification.request.content.userInfo
    let client = info["client_id"] as? String
    let decision = info["decision_id"] as? String
    let action = info["action"] as? String
    return await MainActor.run {
      if let client, self.focus.clientIDs.contains(client) { return [] }
      if let decision, self.focus.decisionIDs.contains(decision) { return [] }
      if let action, self.focus.actionKinds.contains(action) { return [] }
      return [.banner, .list]
    }
  }
  nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
    didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
    let info = response.notification.request.content.userInfo
    let client = info["client_id"] as? String
    let name = info["client_name"] as? String
    let action = info["action"] as? String
    Task { @MainActor [weak self] in
      if let client { self?.openClient(client, name ?? "Client") }
      else if let action { self?.openAction(action) }
    }
    completionHandler()
  }
}
