import FirraKit
import SwiftUI

struct ChangesView: View {
  @State private var model: ChangesModel
  let detail: ClientDetailModel
  let workspaceId: String
  @State private var selected: ClientSelection?

  init(api: APIClient, detail: ClientDetailModel, workspaceId: String) {
    _model = State(initialValue: ChangesModel(api: api))
    self.detail = detail
    self.workspaceId = workspaceId
  }

  var body: some View {
    VStack {
      Picker("Time window", selection: Binding(
        get: { model.windowDays },
        set: { days in Task { await model.setWindowDays(days) } }
      )) {
        ForEach([7, 14, 30], id: \.self) { Text("\($0) days").tag($0) }
      }
      .pickerStyle(.segmented).padding()
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
        if model.items.isEmpty {
          ContentUnavailableView("Nothing changed in the last \(model.windowDays) days", systemImage: "clock")
        } else {
          List {
            ForEach(model.groupedByDay) { group in
              Section(group.day) {
                ForEach(Array(group.items.enumerated()), id: \.offset) { _, item in
                  Button {
                    selected = ClientSelection(id: item.clientId, name: item.clientName)
                  } label: {
                    VStack(alignment: .leading, spacing: 4) {
                      Text(item.clientName).font(.headline)
                      Text(item.summary).font(.callout).foregroundStyle(.secondary)
                    }
                  }
                  .buttonStyle(.plain)
                }
              }
            }
          }
        }
      }
    }
    .navigationTitle("What changed")
    .task(id: workspaceId) {
      selected = nil
      await model.load(workspaceId: workspaceId)
    }
    .sheet(item: $selected) { client in
      VStack(spacing: 0) {
        HStack { Spacer(); Button("Close") { selected = nil }.keyboardShortcut(.cancelAction) }
          .padding([.top, .horizontal])
        ClientDetailView(model: detail, workspaceId: workspaceId, clientId: client.id, name: client.name)
      }
      .frame(minWidth: 620, minHeight: 560)
    }
  }

  private struct ClientSelection: Identifiable {
    let id: String
    let name: String
  }
}
