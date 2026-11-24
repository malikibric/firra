import FirraKit
import SwiftUI

/// Click state: up to three clients that need attention, worst first, and a way into the window.
struct NotchListView: View {
  let state: NotchState
  var send: (NotchEvent) -> Void
  var openFirra: (_ clientId: String?) -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      Button { send(.click) } label: {
        HStack(spacing: 8) {
          Circle().fill(NotchLevelStyle.color(state.level)).frame(width: 8, height: 8)
          Text(state.headline).font(.caption.bold()).foregroundStyle(.white)
          Spacer()
        }
        .contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .accessibilityLabel(state.headline)
      .accessibilityHint("Closes the list")

      if state.topItems.isEmpty {
        Text("Nothing needs attention").font(.callout).foregroundStyle(.secondary)
      } else {
        ForEach(state.topItems) { item in
          Button { openFirra(item.clientId) } label: { NotchListRow(item: item) }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(item.name), \(item.riskState)")
        }
      }
      Spacer(minLength: 0)
      HStack {
        Spacer()
        Button("Open Firra") { openFirra(nil) }.buttonStyle(.link).font(.caption)
      }
    }
    .padding(.horizontal, 16).padding(.bottom, 10)
    .padding(.top, NotchInset.top + 6)
    .onExitCommand { send(.dismiss) }
  }
}

private struct NotchListRow: View {
  let item: AttentionItem

  var body: some View {
    VStack(alignment: .leading, spacing: 2) {
      HStack {
        Text(item.name).font(.callout.bold()).foregroundStyle(.white).lineLimit(1)
        let color = AttentionRow.color(for: item.riskState)
        Text(item.riskState.capitalized)
          .font(.caption2.bold()).padding(.horizontal, 6).padding(.vertical, 1)
          .background(color.opacity(0.25), in: Capsule()).foregroundStyle(color)
        Spacer()
      }
      Text("Since \(item.stateSince)" + (item.play.map { " · " + AttentionRow.title($0) } ?? ""))
        .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
    }
    .contentShape(Rectangle())
  }
}
