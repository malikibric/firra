import CoreGraphics
import Foundation

public struct NotchMetrics: Sendable, Equatable {
  public let screenFrame: CGRect
  public let safeAreaTop: CGFloat
  public let auxLeft: CGRect?
  public let auxRight: CGRect?

  public init(screenFrame: CGRect, safeAreaTop: CGFloat, auxLeft: CGRect?, auxRight: CGRect?) {
    self.screenFrame = screenFrame
    self.safeAreaTop = safeAreaTop
    self.auxLeft = auxLeft
    self.auxRight = auxRight
  }
}

public enum NotchSize { case collapsed, hint, list, meeting }

public enum NotchGeometry {
  public static func notchWidth(_ m: NotchMetrics) -> CGFloat {
    guard let left = m.auxLeft, let right = m.auxRight else { return 0 }
    return right.minX - left.maxX
  }

  public static func panelFrame(_ m: NotchMetrics, size: NotchSize) -> CGRect {
    let notched = m.safeAreaTop > 0
    let collapsed = CGSize(width: notched ? notchWidth(m) + 24 : 120,
      height: notched ? m.safeAreaTop + 8 : 24)
    let dimensions: CGSize
    switch size {
    case .collapsed: dimensions = collapsed
    case .hint: dimensions = CGSize(width: 280, height: collapsed.height + 20)
    case .list: dimensions = CGSize(width: 360, height: 220)
    case .meeting: dimensions = CGSize(width: 380, height: 200)
    }
    let centre: CGFloat
    if notched, let left = m.auxLeft, let right = m.auxRight {
      centre = (left.maxX + right.minX) / 2
    } else { centre = m.screenFrame.midX }
    let width = min(dimensions.width, m.screenFrame.width)
    let x = min(max(centre - width / 2, m.screenFrame.minX), m.screenFrame.maxX - width)
    return CGRect(x: x, y: m.screenFrame.maxY - dimensions.height, width: width, height: dimensions.height)
  }
}
