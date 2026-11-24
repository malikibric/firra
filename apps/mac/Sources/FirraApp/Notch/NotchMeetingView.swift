import FirraKit
import SwiftUI

/// The pre-meeting brief in the pill: who, when, and the questions to ask. Shows only what the
/// provider returned; with no questions it says so.
struct NotchMeetingView: View {
  let brief: MeetingBriefSummary
  var onDismiss: () -> Void
  var openFirra: (_ clientId: String?) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      TimelineView(.periodic(from: .now, by: 30)) { context in
        HStack(alignment: .firstTextBaseline) {
          Text(brief.clientName).font(.callout.bold()).foregroundStyle(.white).lineLimit(1)
          Spacer()
          Text(Self.startText(brief.startsAt, now: context.date)).font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(brief.clientName), \(Self.startText(brief.startsAt, now: context.date))")
      }
      if brief.questions.isEmpty {
        Text("No questions yet").font(.callout).foregroundStyle(.secondary)
      } else {
        ForEach(Array(brief.questions.prefix(3).enumerated()), id: \.offset) { index, question in
          HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text("\(index + 1).").font(.callout).foregroundStyle(.secondary)
            Text(question).font(.callout).foregroundStyle(.white).lineLimit(3)
          }
          .accessibilityElement(children: .combine)
        }
      }
      Spacer(minLength: 0)
      HStack {
        Button("Dismiss", action: onDismiss).buttonStyle(.plain).font(.caption).foregroundStyle(.secondary)
        Spacer()
        Button("Open Firra") { openFirra(brief.clientId) }.buttonStyle(.link).font(.caption)
      }
    }
    .padding(.horizontal, 16).padding(.bottom, 10)
    .padding(.top, NotchInset.top + 6)
    .onExitCommand(perform: onDismiss)
  }

  /// "starts in 9 min", "starting now" under a minute, "started" after the start.
  static func startText(_ start: Date, now: Date) -> String {
    let seconds = start.timeIntervalSince(now)
    if seconds < 0 { return "started" }
    if seconds < 60 { return "starting now" }
    let minutes = Int((seconds / 60).rounded(.down))
    if minutes < 60 { return "starts in \(minutes) min" }
    return "starts in \(minutes / 60) h \(minutes % 60) min"
  }
}
