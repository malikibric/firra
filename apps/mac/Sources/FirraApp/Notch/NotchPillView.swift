import AppKit
import FirraKit
import SwiftUI

/// What the notch pill shows. Only displays `NotchModel.state` and sends events back.
struct NotchPillView: View {
  let model: NotchModel
  /// Activates the main window. `clientId` is a hint for which client to show, nil for the overview.
  var openFirra: (_ clientId: String?) -> Void

  var body: some View {
    NotchPillContent(state: model.state, send: { model.send($0) }, openFirra: openFirra)
  }
}

/// The stateless drawing of one `NotchState`, so every mode can be previewed without a model.
struct NotchPillContent: View {
  let state: NotchState
  var send: (NotchEvent) -> Void
  var openFirra: (_ clientId: String?) -> Void
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    content
      .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
      .background(Color.black, in: UnevenRoundedRectangle(bottomLeadingRadius: 14, bottomTrailingRadius: 14))
      .onHover { send(.hover($0)) }
      .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: state.mode)
  }

  @ViewBuilder private var content: some View {
    switch state.mode {
    case .dot:
      NotchDotView(level: state.level, meetingReady: state.meetingReady)
        .modifier(NotchTapTarget(state: state, send: send))
    case .hint:
      NotchHintView(level: state.level, headline: state.headline, meetingReady: state.meetingReady)
        .modifier(NotchTapTarget(state: state, send: send))
    case .list:
      NotchListView(state: state, send: send, openFirra: openFirra)
    case .meeting(let brief):
      NotchMeetingView(brief: brief, onDismiss: { send(.dismiss) }, openFirra: openFirra)
    }
  }
}

/// `dot` and `hint` are one element for VoiceOver and a click opens the list.
private struct NotchTapTarget: ViewModifier {
  let state: NotchState
  var send: (NotchEvent) -> Void

  func body(content: Content) -> some View {
    content
      .contentShape(Rectangle())
      .onTapGesture { send(.click) }
      .accessibilityElement(children: .ignore)
      .accessibilityLabel(state.headline)
      .accessibilityValue(NotchLevelStyle.name(state.level))
      .accessibilityAddTraits(.isButton)
      .accessibilityAction { send(.click) }
  }
}

enum NotchLevelStyle {
  static func color(_ level: NotchLevel) -> Color {
    switch level {
    case .calm: .green
    case .watch: .yellow
    case .elevated: .orange
    case .high: .red
    }
  }

  static func name(_ level: NotchLevel) -> String {
    switch level {
    case .calm: "Calm"
    case .watch: "Watch"
    case .elevated: "Elevated"
    case .high: "High"
    }
  }
}

/// Height of the notch (0 on a Mac without one), so content can start below it.
enum NotchInset {
  static var top: CGFloat {
    NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 })?.safeAreaInsets.top ?? 0
  }
}

#if DEBUG
  enum NotchPreviewData {
    static func items(_ states: [(String, String, String?)]) -> [AttentionItem] {
      let decoder = JSONDecoder()
      decoder.keyDecodingStrategy = .convertFromSnakeCase
      let rows = states.enumerated().map { index, row -> String in
        """
        {"client_id":"c\(index)","name":"\(row.0)","status":"active","policy":"full","as_of":"2026-10-03",
         "risk_state":"\(row.1)","previous_risk_state":null,"state_since":"2026-09-20",
         "expansion_state":"none","strength":"medium",
         "play":\(row.2.map { "\"\($0)\"" } ?? "null"),"days_to_notice":null,"drivers":[]}
        """
      }
      let json = "[" + rows.joined(separator: ",") + "]"
      return (try? decoder.decode([AttentionItem].self, from: Data(json.utf8))) ?? []
    }

    static func state(_ items: [AttentionItem], mode: NotchEvent? = nil) -> NotchState {
      var state = NotchState()
      state.apply(.items(items))
      if let mode { state.apply(mode) }
      return state
    }

    static let questions = ["Who owns the reporting deadline?", "Is the renewal still on the table?", "Who signs off the roadmap now?"]
    static let longQuestions = Array(
      repeating: String(repeating: "Which of the three open requests from the last call is blocking the launch? ", count: 2),
      count: 3)

    static func meeting(questions: [String]) -> NotchState {
      let start = Date().addingTimeInterval(9 * 60)
      let brief = MeetingBriefSummary(
        id: "p", clientId: "c0", clientName: "Kestrel Labs", startsAt: start, questions: questions)
      var state = NotchState()
      state.apply(.meetings([brief]))
      state.apply(.tick(Date()))
      return state
    }

    static let two = items([("Kestrel Labs", "high", "save_the_account"), ("Vantage Co", "elevated", "renewal_prep")])
    static let three = items([
      ("Kestrel Labs", "high", "save_the_account"), ("Vantage Co", "elevated", "renewal_prep"),
      ("Northwind", "watch", nil),
    ])
  }

  /// Xcode canvas previews (`PreviewProvider` because the `#Preview` macro needs a full Xcode toolchain).
  struct NotchPillPreviews: PreviewProvider {
    private static func pill(_ state: NotchState, _ width: CGFloat, _ height: CGFloat) -> some View {
      NotchPillContent(state: state, send: { _ in }, openFirra: { _ in }).frame(width: width, height: height)
    }

    static var previews: some View {
      Group {
        pill(NotchPreviewData.state(NotchPreviewData.items([("Acme", "calm", nil)])), 224, 46).previewDisplayName("Calm dot")
        pill(NotchPreviewData.state(NotchPreviewData.items([("Acme", "watch", nil)])), 224, 46).previewDisplayName("Watch dot")
        pill(NotchPreviewData.state(NotchPreviewData.two, mode: .hover(true)), 280, 66).previewDisplayName("Elevated hint")
        pill(NotchPreviewData.state(NotchPreviewData.three, mode: .click), 360, 220).previewDisplayName("High list, 3 items")
        pill(NotchPreviewData.state([], mode: .click), 360, 220).previewDisplayName("Empty list")
        pill(NotchPreviewData.meeting(questions: NotchPreviewData.questions), 380, 200).previewDisplayName("Meeting")
        pill(NotchPreviewData.meeting(questions: []), 380, 200).previewDisplayName("Meeting, no questions")
        pill(NotchPreviewData.meeting(questions: NotchPreviewData.longQuestions), 380, 200).previewDisplayName("Meeting, long questions")
      }
    }
  }
#endif
