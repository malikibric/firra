import Foundation
import Observation

/// "Start Firra when I log in" (plan L5-01). The real implementation is `SMAppService.mainApp`; macOS may refuse
/// it for an ad-hoc signed app (risk R7), in which case the owner is told to add Firra under Login Items by hand.
public enum LoginItemStatus: Sendable, Equatable { case enabled, disabled, needsApproval, unavailable }

public struct LoginItemActions: Sendable {
  public var status: @Sendable () -> LoginItemStatus
  public var register: @Sendable () throws -> Void
  public var unregister: @Sendable () throws -> Void
  public init(
    status: @escaping @Sendable () -> LoginItemStatus, register: @escaping @Sendable () throws -> Void,
    unregister: @escaping @Sendable () throws -> Void
  ) { self.status = status; self.register = register; self.unregister = unregister }
}

@MainActor @Observable public final class LoginItemModel {
  public static let manualHint =
    "macOS did not allow this. Add Firra yourself in System Settings, General, Login Items."
  public private(set) var status: LoginItemStatus
  public private(set) var message: String?
  private let actions: LoginItemActions
  public init(actions: LoginItemActions) { self.actions = actions; status = actions.status() }

  public var isOn: Bool { status == .enabled || status == .needsApproval }
  public func refresh() { status = actions.status() }
  public func set(_ on: Bool) {
    message = nil
    do {
      if on { try actions.register() } else { try actions.unregister() }
    } catch { message = Self.manualHint }
    status = actions.status()
    if on, status == .disabled || status == .unavailable { message = Self.manualHint }
    if status == .needsApproval { message = "Approve Firra in System Settings, General, Login Items." }
  }
}
