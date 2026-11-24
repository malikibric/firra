import FirraKit
import SwiftUI

/// Mailboxes connected to the workspace and a way to connect your own.
struct ConnectionsView: View {
  @Environment(NotificationFocus.self) private var notificationFocus
  let model: ConnectionsModel
  let workspaceId: String
  @State private var removing: LocalStatus.Connection?

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack {
        Text("Google account").font(.title2.bold())
        Spacer()

      }
      Text("Firra reads only Google mail and calendar entries that involve a client.")
        .foregroundStyle(.secondary)
      if let message = model.message { Text(message).foregroundStyle(.red) }
      switch model.phase {
      case .idle, .loading:
        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
      case .failed(let text):
        ContentUnavailableView {
          Label("Could not load", systemImage: "exclamationmark.triangle")
        } description: {
          Text(text)
        } actions: {
          Button("Try again") { Task { await model.load(workspaceId: workspaceId) } }
        }
      case .ready:
        List(model.connections) { connection in
          HStack {
            VStack(alignment: .leading, spacing: 2) {
              Text(connection.accountLabel)
              Text(subtitle(connection)).font(.caption)
                .foregroundStyle(connection.state == "needs_reauth" ? .orange : .secondary)
            }
            Spacer()
            Button("Reconnect") { Task { await model.reconnect(workspaceId: workspaceId, connection: connection) } }
              .buttonStyle(.link)
            Button("Disconnect…", role: .destructive) { removing = connection }.buttonStyle(.link)
          }
        }
        .overlay {
          if model.connections.isEmpty {
            ContentUnavailableView(
              "Nothing connected", systemImage: "envelope",
              description: Text("Connect your mailbox to start."))
          }
        }
      }
    }
    .onAppear { notificationFocus.actionKinds.insert("google") }
    .onDisappear { notificationFocus.actionKinds.remove("google") }
    .padding()
    .task(id: workspaceId) { await model.load(workspaceId: workspaceId) }
    .confirmationDialog(
      "Disconnect this mailbox?", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
      titleVisibility: .visible
    ) {
      Button("Disconnect", role: .destructive) {
        if let removing { Task { await model.disconnect(workspaceId: workspaceId, connectionId: removing.id) } }
      }
    } message: {
      Text("Firra stops reading it. Facts already drawn from it stay.")
    }
  }

  private func subtitle(_ connection: LocalStatus.Connection) -> String {
    var parts = [connection.provider.replacingOccurrences(of: "_", with: " ")]
    switch connection.state {
    case "idle", "syncing": break
    case "needs_reauth": parts.append("access was revoked — reconnect")
    default: parts.append("needs attention")
    }
    return parts.joined(separator: " · ")
  }
}
