import FirraKit
import ServiceManagement

extension LoginItemActions {
  /// The real login item of this app bundle.
  static let system = LoginItemActions(
    status: {
      switch SMAppService.mainApp.status {
      case .enabled: .enabled
      case .requiresApproval: .needsApproval
      case .notRegistered: .disabled
      case .notFound: .unavailable
      @unknown default: .unavailable
      }
    },
    register: { try SMAppService.mainApp.register() },
    unregister: { try SMAppService.mainApp.unregister() })
}
