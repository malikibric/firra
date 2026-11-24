import FirraKit
import SwiftUI

/// Hover state: the headline next to the dot.
struct NotchHintView: View {
  let level: NotchLevel
  let headline: String
  let meetingReady: Bool

  var body: some View {
    VStack(spacing: 0) {
      Spacer(minLength: 0)
      HStack(spacing: 8) {
        NotchDotView(level: level, meetingReady: meetingReady).frame(width: 14, height: 14)
        Text(headline).font(.caption).foregroundStyle(.white).lineLimit(1)
      }
      .padding(.bottom, 6)
    }
    .frame(maxWidth: .infinity)
  }
}
