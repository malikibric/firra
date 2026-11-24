import AppKit
import FirraKit
import SwiftUI

struct MenuBarContent: View {
  let model: AppModel
  @Environment(\.openWindow) private var openWindow

  private func syncTime(_ value: String?) -> String {
    guard let date = MenuStatusModel.syncDate(value) else { return "not yet" }
    return date.formatted(date: .abbreviated, time: .shortened)
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if model.wiping {
        Text("Deleting Firra data").font(.headline)
        if let remaining = model.maintenance?.remaining { Text(remaining).font(.caption) }
      } else if case .running = model.local.state, let services = model.services {
        let menu = services.menu
        Text(menu.title).font(.headline)
        if menu.health == .stopped { Button("Restart") { model.restart() } }
        ForEach(menu.attention.prefix(3)) { item in
          Button { model.showClient(item.clientId, name: item.name) } label: {
            VStack(alignment: .leading) {
              Text("\(item.name): \(item.riskState)")
              Text(item.drivers.first?.type.replacingOccurrences(of: "_", with: " ") ?? "Assessment needs attention")
                .font(.caption).lineLimit(1)
            }
          }.buttonStyle(.link)
        }
        if let waiting = menu.waiting { Text(waiting).font(.caption) }
        ForEach(menu.connections) { connection in
          Text("\(connection.provider == "gmail" ? "Gmail" : "Calendar"): last sync \(syncTime(connection.lastSyncedAt))")
            .font(.caption)
        }
        if menu.actions.contains(.google) {
          Button("Reconnect Google") { model.reconnectGoogle() }
          if let message = services.connections.message { Text(message).font(.caption) }
        }
        if menu.actions.contains(.claude) {
          Button("Sign in to Claude Code") { model.showAction("claude") }
          Text("Install Claude Code if needed, then run claude auth login in a terminal.").font(.caption)
        }
        Button("Sync now") { Task { await menu.syncNow() } }.disabled(menu.connections.isEmpty)
        if let message = menu.message { Text(message).font(.caption) }

      } else {
        switch model.local.state {
        case .stopped, .failed:
          Text("Firra stopped.")
          Button("Restart") { model.restart() }
        case .restarting: Text("Firra is restarting…")
        default: Text("Firra is starting…")
        }
      }
      Toggle("Show notch pill", isOn: Binding(
        get: { model.notchEnabled }, set: { model.notchEnabled = $0 }))
      Divider()
      Button("Open Firra") {
        openWindow(id: "main")
        NSApplication.shared.activate(ignoringOtherApps: true)
      }
      Button("Quit Firra") {
        Task { await model.stop(); NSApplication.shared.terminate(nil) }
      }
    }
    .padding(12).frame(width: 260)
  }
}
