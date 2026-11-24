import FirraKit
import SwiftUI

struct RootView: View {
  @Bindable var model: AppModel
  @State private var connectingAI = false
  @State private var connectingSlack = false

  var body: some View {
    Group {
      if model.wiping, let maintenance = model.maintenance {
        SettingsDataView(model: maintenance, ai: nil)
      } else if case .running = model.local.state, let services = model.services {
        if services.onboarding.step == .done {
          MainView(services: services, updates: model.updates, loginItem: model.loginItem, connectAI: { connectingAI = true }, connectSlack: { connectingSlack = true }).environment(services.menu.focus)
        } else { OnboardingView(model: services.onboarding, connectAI: { connectingAI = true }, connectSlack: { connectingSlack = true }).environment(services.menu.focus) }
      } else {
        VStack(spacing: 16) {
          switch model.local.state {
          case .stopped, .failed:
            Text("Firra stopped.").font(.headline)
            Button("Restart") { model.restart() }
          case .restarting:
            ProgressView("Firra is restarting…")
          default:
            ProgressView("Firra is starting…")
          }
          if let problem = model.local.lastFailure {
            Text("Last problem: \(problem)").font(.caption).foregroundStyle(.secondary)
              .textSelection(.enabled).multilineTextAlignment(.center).padding(.horizontal)
          }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
      }
    }.sheet(item: $model.requestedClient) { client in
      if let services = model.services {
        VStack {
          Button("Close") { model.requestedClient = nil }
          ClientDetailView(model: services.clientDetail, workspaceId: services.workspaceId,
            clientId: client.id, name: client.name)
        }.frame(minWidth: 620, minHeight: 560).environment(services.menu.focus)
      }
    }.alert("Firra needs you", isPresented: Binding(
      get: { model.requestedAction != nil }, set: { if !$0 { model.dismissAction() } })) {
      if model.requestedAction == "google" { Button("Reconnect Google") { model.reconnectGoogle() } }
      Button("Close", role: .cancel) { model.dismissAction() }
    } message: {
      Text(model.requestedAction == "google" ? "Google needs to be reconnected." :
        "Install Claude Code if needed, then run claude auth login in a terminal.")
    }.sheet(isPresented: $connectingSlack) {
      if let services = model.services { SlackConnectView(model: services.slack) }
    }.sheet(isPresented: $connectingAI) {
      if let services = model.services {
        ConnectAIToolsView(api: services.api, sourceAccess: services.sourceAccess, processingTools: services.processingTools)
      }
    }
  }
}
