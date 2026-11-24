import Foundation
import Testing
@testable import FirraKit

@Suite struct NotchGeometryTests {
  let notch = NotchMetrics(screenFrame: CGRect(x: 0, y: 0, width: 1512, height: 982), safeAreaTop: 38,
    auxLeft: CGRect(x: 0, y: 944, width: 656, height: 38),
    auxRight: CGRect(x: 856, y: 944, width: 656, height: 38))

  @Test(arguments: [0, 1, 2, 3])
  func notchFrames(_ index: Int) {
    let size: NotchSize = [.collapsed, .hint, .list, .meeting][index]
    #expect(NotchGeometry.notchWidth(notch) == 200)
    let expected: CGSize
    switch size {
    case .collapsed: expected = CGSize(width: 224, height: 46)
    case .hint: expected = CGSize(width: 280, height: 66)
    case .list: expected = CGSize(width: 360, height: 220)
    case .meeting: expected = CGSize(width: 380, height: 200)
    }
    let frame = NotchGeometry.panelFrame(notch, size: size)
    #expect(frame.size == expected && frame.midX == 756 && frame.maxY == 982)
  }

  @Test func noNotchAndMissingAuxiliaryAreas() {
    let screen = CGRect(x: 0, y: 0, width: 1920, height: 1080)
    let plain = NotchMetrics(screenFrame: screen, safeAreaTop: 0, auxLeft: nil, auxRight: nil)
    #expect(NotchGeometry.notchWidth(plain) == 0)
    #expect(NotchGeometry.panelFrame(plain, size: .collapsed) == CGRect(x: 900, y: 1056, width: 120, height: 24))
    #expect(NotchGeometry.panelFrame(plain, size: .hint) == CGRect(x: 820, y: 1036, width: 280, height: 44))
    let missing = NotchMetrics(screenFrame: screen, safeAreaTop: 38, auxLeft: notch.auxLeft, auxRight: nil)
    #expect(NotchGeometry.notchWidth(missing) == 0)
    #expect(NotchGeometry.panelFrame(missing, size: .collapsed).midX == screen.midX)
  }

  @Test func secondDisplayUsesGapCentreAndGlobalTopEdge() {
    let second = NotchMetrics(screenFrame: CGRect(x: -1512, y: 100, width: 1512, height: 982), safeAreaTop: 38,
      auxLeft: CGRect(x: -1512, y: 1044, width: 600, height: 38),
      auxRight: CGRect(x: -712, y: 1044, width: 712, height: 38))
    let frame = NotchGeometry.panelFrame(second, size: .collapsed)
    #expect(frame == CGRect(x: -924, y: 1036, width: 224, height: 46))
    let plain = NotchMetrics(screenFrame: second.screenFrame, safeAreaTop: 0, auxLeft: nil, auxRight: nil)
    #expect(NotchGeometry.panelFrame(plain, size: .collapsed).midX == -756)
  }

  @Test(arguments: [0, 1, 2, 3])
  func narrowScreensClampEverySize(_ index: Int) {
    let size: NotchSize = [.collapsed, .hint, .list, .meeting][index]
    for top in [CGFloat(0), 38] {
      let screen = CGRect(x: 50, y: -300, width: 100, height: 600)
      let metrics = NotchMetrics(screenFrame: screen, safeAreaTop: top,
        auxLeft: CGRect(x: 50, y: 262, width: 5, height: 38),
        auxRight: CGRect(x: 255, y: 262, width: 5, height: 38))
      let frame = NotchGeometry.panelFrame(metrics, size: size)
      #expect(frame.width == 100 && frame.minX == 50 && frame.maxY == 300)
    }
  }
}
