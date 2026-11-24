import AppKit
import FirraKit
import Observation

@MainActor final class NotchController: NSObject, NSWindowDelegate {
  private let model: NotchModel
  private(set) var panel: NotchPanel?
  private var generation = 0

  init(model: NotchModel) { self.model = model }

  /// Brings the main Firra window to the front. The client id is only a hint; the window keeps its own navigation.
  private func openFirra(_ clientId: String?) {
    model.send(.dismiss)
    NSApplication.shared.activate(ignoringOtherApps: true)
    NSApplication.shared.windows.first(where: { $0.title == "Firra" && !($0 is NotchPanel) })?
      .makeKeyAndOrderFront(nil)
  }

  func start() {
    guard panel == nil else { return }
    let panel = NotchPanel()
    panel.contentView = NotchHostingView(
      rootView: NotchPillView(model: model, openFirra: { [weak self] in self?.openFirra($0) }))
    panel.delegate = self
    panel.dismiss = { [weak model] in model?.send(.dismiss) }
    self.panel = panel
    NotificationCenter.default.addObserver(self, selector: #selector(screensChanged),
      name: NSApplication.didChangeScreenParametersNotification, object: nil)
    position(animated: false)
    panel.orderFrontRegardless()
    observeMode()
  }

  func stop() {
    generation += 1
    NotificationCenter.default.removeObserver(self)
    panel?.delegate = nil
    panel?.dismiss = nil
    panel?.close()
    panel = nil
  }

  func windowDidResignKey(_ notification: Notification) {
    if model.state.mode == .list { model.send(.dismiss) }
  }

  @objc private func screensChanged() { position(animated: false) }

  private func observeMode() {
    guard panel != nil else { return }
    let observed = generation
    let mode = model.state.mode
    withObservationTracking {
      _ = model.state.mode
    } onChange: { [weak self] in
      Task { @MainActor in
        guard let self, self.panel != nil, self.generation == observed else { return }
        if self.model.state.mode != mode {
          self.position(animated: true)
          switch self.model.state.mode {
          case .list, .meeting: self.panel?.makeKeyAndOrderFront(nil)
          default: break
          }
        }
        self.observeMode()
      }
    }
  }

  private func position(animated: Bool) {
    guard let panel,
      let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) ?? NSScreen.main
    else { return }
    let size: NotchSize
    switch model.state.mode {
    case .dot: size = .collapsed
    case .hint: size = .hint
    case .list: size = .list
    case .meeting: size = .meeting
    }
    let metrics = NotchMetrics(screenFrame: screen.frame, safeAreaTop: screen.safeAreaInsets.top,
      auxLeft: screen.auxiliaryTopLeftArea, auxRight: screen.auxiliaryTopRightArea)
    let frame = NotchGeometry.panelFrame(metrics, size: size)
    panel.setFrame(frame, display: true,
      animate: animated && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion)
  }
}
