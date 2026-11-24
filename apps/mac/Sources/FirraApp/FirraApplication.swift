import AppKit
import FirraKit
import SwiftUI

@main
struct FirraApplication: App {
  @State private var model = AppModel()

  var body: some Scene {
    Window("Firra", id: "main") {
      RootView(model: model)
        .frame(minWidth: 760, minHeight: 480)
        .onAppear { model.start() }

    }
    .defaultSize(width: 980, height: 640)

    MenuBarExtra {
      MenuBarContent(model: model)
    } label: {
      MenuBarLabel(model: model)
    }
    .menuBarExtraStyle(.window)
  }
}

private struct MenuBarLabel: View {
  let model: AppModel
  @Environment(\.openWindow) private var openWindow
  var body: some View {
    Label(model.menuTitle, systemImage: model.menuIcon)
      .onReceive(NotificationCenter.default.publisher(for: .firraOpenMain)) { _ in
        openWindow(id: "main")
        NSApplication.shared.activate(ignoringOtherApps: true)
      }
  }
}
