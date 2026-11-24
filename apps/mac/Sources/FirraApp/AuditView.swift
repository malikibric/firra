import FirraKit
import SwiftUI

/// Who did what in the workspace, including every read of client data.
struct AuditView: View {
  let model: AuditModel
  let workspaceId: String

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text("Audit log").font(.title2.bold())
      Text("Every read of client data and every change to the workspace, newest first.")
        .foregroundStyle(.secondary)
      switch model.phase {
      case .idle, .loading:
        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
      case .failed(let text):
        ContentUnavailableView {
          Label("Could not load", systemImage: "exclamationmark.triangle")
        } description: {
          Text(text)
        } actions: {
          Button("Try again") { Task { await model.load(workspaceId: workspaceId) } }
        }
      case .ready:
        List {
          ForEach(model.entries) { entry in
            VStack(alignment: .leading, spacing: 2) {
              Text(entry.action).font(.body.monospaced())
              Text(Self.detail(entry)).font(.caption).foregroundStyle(.secondary)
            }
          }
          if model.nextPage != nil {
            HStack {
              Button("Load more") { Task { await model.loadMore(workspaceId: workspaceId) } }
                .buttonStyle(.link)
              if model.loadMoreFailed {
                Text("Could not load more. Try again.").font(.caption).foregroundStyle(.red)
              }
            }
          }
        }
      }
    }
    .padding()
    .task(id: workspaceId) { await model.load(workspaceId: workspaceId) }
  }

  private static func detail(_ entry: AuditEntry) -> String {
    let when = ISO8601DateFormatter().date(from: entry.createdAt)?
      .formatted(date: .abbreviated, time: .shortened) ?? entry.createdAt
    let who = entry.actorId.map { String($0.prefix(8)) } ?? entry.actorType
    return "\(when) · \(entry.actorType) \(who)"
  }
}
