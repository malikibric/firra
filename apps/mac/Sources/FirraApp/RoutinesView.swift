import FirraKit
import SwiftUI

struct RoutinesView: View {
  let model: RoutinesModel
  let workspaceId: String

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text("Routines").font(.title2.bold())
      switch model.workspaceId == workspaceId ? model.phase : .idle {
      case .idle, .loading:
        ProgressView("Loading routines")
          .accessibilityLabel("Loading routine settings")
      case .failed(let text):
        ContentUnavailableView {
          Label("Could not load routines", systemImage: "exclamationmark.triangle")
        } description: {
          Text(text)
        } actions: {
          retry
        }
        .accessibilityLabel("Could not load routine settings. \(text)")
      case .ready:
        if model.isStale {
          Label("Settings may be out of date. Try again before making changes.", systemImage: "exclamationmark.triangle")
        }
        if let message = model.message {
          Text(message).foregroundStyle(.red).accessibilityLabel("Routine settings error: \(message)")
          retry
        }
        Form {
          ForEach(model.items) { item in
            VStack(alignment: .leading, spacing: 6) {
              Toggle(isOn: Binding(
                get: { item.enabled },
                set: { enabled in
                  Task { await model.setEnabled(workspaceId: workspaceId, kind: item.kind, enabled: enabled) }
                }
              )) { title(item.kind) }
              .disabled(!model.canEdit)
              .accessibilityLabel(title(item.kind))
              explanation(item.kind).font(.callout).foregroundStyle(.secondary)
              if let run = item.lastRun {
                Text("Last run \(run.scheduledFor) · \(run.displayStatus)")
                  .font(.caption).foregroundStyle(.secondary)
              } else {
                Text("Not run yet").font(.caption).foregroundStyle(.secondary)
              }
              if model.saving == item.kind {
                ProgressView("Saving routine setting").accessibilityLabel("Saving routine setting")
              }
            }
          }
        }
        .formStyle(.grouped)
        if model.isLoading {
          ProgressView("Refreshing routines").accessibilityLabel("Refreshing routine settings")
        }
      }
    }
    .padding()
    .task(id: workspaceId) { await model.load(workspaceId: workspaceId) }
  }

  private var retry: some View {
    Button("Try again") { Task { await model.load(workspaceId: workspaceId) } }
      .disabled(model.isLoading || model.saving != nil)
  }

  private func title(_ kind: RoutineKind) -> Text {
    switch kind {
    case .morningScan: Text("Morning scan")
    case .commitmentWatch: Text("Commitment watch")
    case .preMeetingBrief: Text("Pre-meeting brief")
    }
  }

  private func explanation(_ kind: RoutineKind) -> Text {
    switch kind {
    case .morningScan:
      Text("Surfaces meaningful client changes on weekdays after 07:30, in your workspace’s local time.")
    case .commitmentWatch:
      Text("Surfaces newly late commitments on weekdays after 08:00, in your workspace’s local time.")
    case .preMeetingBrief:
      Text("Summarises client context before meetings. This switch is reserved for the future routine; existing meeting briefs run separately.")
    }
  }
}
