import AppKit
import FirraKit
import SwiftUI

struct ConnectAIToolsView: View {
  @State private var model: ConnectAIToolsModel
  @Environment(\.dismiss) private var dismiss
  init(api: APIClient, sourceAccess: SourceAccessModel, processingTools: SourceAccessModel) {
    _model = State(initialValue: ConnectAIToolsModel(api: api, sourceAccess: sourceAccess, processingTools: processingTools, copy: { value in
      NSPasteboard.general.clearContents()
      return NSPasteboard.general.setString(value, forType: .string)
    }, open: { NSWorkspace.shared.open($0) }))
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack {
        Text("Connect AI tools").font(.title)
        Spacer()
        Button("Done") { dismiss() }
      }
      if model.loading { ProgressView("Loading connection instructions…") }
      if let error = model.error {
        Text(error)
        Button("Retry") { Task { await model.load() } }.disabled(model.loading)
      }
      ScrollView {
        VStack(alignment: .leading, spacing: 20) {
          SourceAccessSwitch(model: model.sourceAccess)
          SourceAccessSwitch(model: model.processingTools)
          Divider()
          ForEach(model.items) { item in
            VStack(alignment: .leading, spacing: 8) {
              Text(item.title).font(.headline)
              if item.kind == .deeplink {
                Button("Add to \(item.title)") { model.act(on: item) }
              } else {
                Text(item.value).font(.system(.body, design: .monospaced))
                  .textSelection(.enabled).padding(10)
                  .frame(maxWidth: .infinity, alignment: .leading)
                  .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                Button("Copy") { model.act(on: item) }
                if item.kind == .command { Text("Run this in a terminal").font(.caption) }
              }
              if let note = item.note { Text(note).font(.caption) }
            }
          }
          Divider()
          RelayLinksView(model: model.relay)
          Text("Try asking: Which clients need my attention?")
        }.frame(maxWidth: .infinity, alignment: .leading)
      }
      if let message = model.actionMessage { Text(message) }
      Button("Test the connection") { Task { await model.testConnection() } }.disabled(model.testing)
      if let message = model.connectionMessage { Text(message) }
    }.padding(24).frame(width: 680, height: 620).task { await model.load() }
      .onDisappear { model.relay.dismissCreatedLink() }
  }
}
