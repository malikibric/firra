import AppKit
import FirraKit
import SwiftUI

final class NotchPanel: NSPanel {
  var dismiss: (() -> Void)?
  override var canBecomeKey: Bool { true }
  override var canBecomeMain: Bool { false }

  init() {
    super.init(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
      backing: .buffered, defer: false)
    level = .statusBar
    isOpaque = false
    backgroundColor = .clear
    hasShadow = false
    collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
    hidesOnDeactivate = false
    isReleasedWhenClosed = false
    becomesKeyOnlyIfNeeded = true
  }

  override func animationResizeTime(_ newFrame: NSRect) -> TimeInterval { 0.2 }

  override func cancelOperation(_ sender: Any?) { dismiss?() }
}

/// The first click must reach SwiftUI even when another app owns keyboard focus.
final class NotchHostingView: NSHostingView<NotchPillView> {
  override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}
