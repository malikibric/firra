import FirraKit
import SwiftUI

private enum Section: String, CaseIterable, Identifiable {
  case attention = "Needs attention"
  case changes = "What changed"
  case decisions = "Decisions"
  case clients = "Clients"
  case connections = "Google account"
  case replay = "Replay history"
  case audit = "Activity"
  case routines = "Routines"
  case services = "Services"
  case settings = "Settings"
  var id: String { rawValue }
  var icon: String {
    switch self {
    case .attention: "exclamationmark.bubble"
    case .changes: "arrow.triangle.2.circlepath"
    case .decisions: "checklist"
    case .clients: "person.2"
    case .connections: "envelope.badge"
    case .replay: "clock.arrow.circlepath"
    case .audit: "list.bullet.clipboard"
    case .routines: "arrow.triangle.2.circlepath"
    case .services: "briefcase"
    case .settings: "gear"
    }
  }
}

struct MainView: View {
  let services: AppModel.Services
  let updates: UpdateModel
  let loginItem: LoginItemModel
  let connectAI: () -> Void
  let connectSlack: () -> Void
  @State private var section: Section? = .attention

  var body: some View {
    NavigationSplitView {
      List(Section.allCases, selection: $section) { item in
        Label(item.rawValue, systemImage: item.icon).tag(item)
      }

      .navigationSplitViewColumnWidth(min: 190, ideal: 210)
    } detail: {
      let workspaceId = services.workspaceId
      switch section ?? .attention {
      case .attention:
        AttentionView(model: services.attention, detail: services.clientDetail, workspaceId: workspaceId)
      case .changes:
        ChangesView(api: services.api, detail: services.clientDetail, workspaceId: workspaceId)
      case .decisions: DecisionsView(model: services.decisions, workspaceId: workspaceId)
      case .clients:
        ClientsView(model: services.clients, detail: services.clientDetail, workspaceId: workspaceId)
      case .connections: ConnectionsView(model: services.connections, workspaceId: workspaceId)
      case .replay: ReplayView(model: services.replay, workspaceId: workspaceId)
      case .audit: AuditView(model: services.audit, workspaceId: workspaceId)
      case .routines: RoutinesView(model: services.routines, workspaceId: workspaceId)
      case .services: ServicesView(model: services.services, workspaceId: workspaceId)
      case .settings:
        SettingsDataView(model: services.data, ai: services.aiSettings, updates: updates, loginItem: loginItem, connectAI: connectAI, connectSlack: connectSlack,
          runSetup: { services.onboarding.runAgain() })
      }
    }
  }
}
