import FirraKit
import SwiftUI

struct AttentionView: View {
  let model: AttentionModel
  let detail: ClientDetailModel
  let workspaceId: String
  @State private var selected: AttentionItem?

  var body: some View {
    Group {
      switch model.phase {
      case .idle, .loading where model.items.isEmpty:
        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
      case .failed(let text):
        ContentUnavailableView {
          Label("Could not load", systemImage: "exclamationmark.triangle")
        } description: {
          Text(text)
        } actions: {
          Button("Try again") { Task { await model.load(workspaceId: workspaceId) } }
        }
      default:
        if model.items.isEmpty {
          ContentUnavailableView(
            "Nothing needs attention", systemImage: "checkmark.circle",
            description: Text("Firra will show clients here when their relationship changes."))
        } else {
          if model.isOffline {
            Label("Offline: this is the last list Firra saved; it could not be refreshed.", systemImage: "wifi.slash")
              .font(.callout).foregroundStyle(.orange).padding(.horizontal)
          }
          List(model.items) { item in
            Button { selected = item } label: { AttentionRow(item: item) }.buttonStyle(.plain)
          }
        }
      }
    }
    .navigationTitle("Needs attention")
    .toolbar {
      Button { Task { await model.load(workspaceId: workspaceId) } } label: {
        Label("Refresh", systemImage: "arrow.clockwise")
      }
    }
    .task(id: workspaceId) { await model.load(workspaceId: workspaceId) }
    .sheet(item: $selected) { item in
      VStack(spacing: 0) {
        HStack { Spacer(); Button("Close") { selected = nil }.keyboardShortcut(.cancelAction) }.padding([.top, .horizontal])
        ClientDetailView(
          model: detail, workspaceId: workspaceId, clientId: item.clientId, name: item.name)
      }
      .frame(minWidth: 620, minHeight: 560)
    }
  }
}

struct AttentionRow: View {
  let item: AttentionItem

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      HStack {
        Text(item.name).font(.headline)
        Text(item.riskState.capitalized)
          .font(.caption.bold()).padding(.horizontal, 6).padding(.vertical, 2)
          .background(color.opacity(0.18), in: Capsule()).foregroundStyle(color)
        Spacer()
        if let days = item.daysToNotice {
          Text("\(days) days to notice").font(.caption).foregroundStyle(.secondary)
        }
      }
      Text(
        (item.previousRiskState.map { "Was \($0), " } ?? "") + "since \(item.stateSince)"
          + (item.play.map { " · suggested: \(Self.title($0))" } ?? "")
          + (item.expansionState == "none" ? "" : " · growth: \(item.expansionState)"))
        .font(.callout).foregroundStyle(.secondary)
      if !item.drivers.isEmpty {
        Text(item.drivers.map { Self.title($0.type) }.joined(separator: " · "))
          .font(.caption).foregroundStyle(.secondary)
      }
    }
    .padding(.vertical, 4)
  }

  private var color: Color { Self.color(for: item.riskState) }

  static func color(for riskState: String) -> Color {
    switch riskState {
    case "high": .red
    case "elevated": .orange
    case "watch": .yellow
    default: .green
    }
  }

  /// `renewal_prep` → "Renewal prep".
  static func title(_ raw: String) -> String {
    raw.replacingOccurrences(of: "_", with: " ").capitalized
  }
}
