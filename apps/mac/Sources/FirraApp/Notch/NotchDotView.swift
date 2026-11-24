import FirraKit
import SwiftUI

/// Idle pill: one dot in the worst client's risk colour, with a badge when a meeting brief is ready.
struct NotchDotView: View {
  let level: NotchLevel
  let meetingReady: Bool

  var body: some View {
    VStack(spacing: 0) {
      Spacer(minLength: 0)
      Circle()
        .fill(NotchLevelStyle.color(level))
        .frame(width: 8, height: 8)
        .overlay(alignment: .topTrailing) {
          if meetingReady { Circle().fill(Color.white).frame(width: 5, height: 5).offset(x: 4, y: -4) }
        }
        .padding(.bottom, 2)
    }
    .frame(maxWidth: .infinity)
  }
}
