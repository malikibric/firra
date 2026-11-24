import FirraKit
import SwiftUI

struct RelayLinksView: View {
  @Bindable var model: RelayLinksModel
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text("claude.ai and ChatGPT (over a link)").font(.headline)
      if model.settings?.relayUrl == nil {
        TextField("Relay address (https://host)", text: $model.address)
        Button("Set relay address") { Task { await model.saveAddress() } }.disabled(model.busy)
      }
      Text(model.settings?.state.text ?? "Checking relay status…")
      Text("When this Mac sleeps or Firra is closed, your AI gets ‘Firra on your Mac is offline’.").font(.caption)
      if model.busy { ProgressView("Updating connector links…") }
      if let error = model.error {
        Text(error).foregroundStyle(.orange)
        Button("Refresh relay status") { Task { await model.load() } }.disabled(model.busy)
      }
      TextField("Link name", text: $model.name)
      Toggle("Firra's client intelligence", isOn: .constant(true)).disabled(true)
      Toggle("Let this AI read excerpts of one client's mail and Slack", isOn: $model.sources)
      Text(SourceAccessModel.explanation).font(.caption)
      Text("The separate Settings source-access switch above must also be on.").font(.caption)
      Button("Create connector link") { Task { await model.create() } }.disabled(model.busy)
      ForEach(model.settings?.links ?? []) { link in
        VStack(alignment: .leading, spacing: 4) {
          Text(link.name).font(.headline)
          Text(link.scopeText).font(.caption)
          if let date = MenuStatusModel.syncDate(link.createdAt) { Text("Created \(date.formatted())").font(.caption) }
          Button("Revoke…", role: .destructive) { model.requestRevoke(link) }.disabled(model.busy)
        }
      }
    }.sheet(isPresented: Binding(get: { model.createdURL != nil }, set: { shown in
      if !shown { model.dismissCreatedLink() }
    }), onDismiss: model.dismissCreatedLink) {
      VStack(alignment: .leading, spacing: 16) {
        Text("Your connector link").font(.title2)
        Text(RelayLinksModel.linkExplanation)
        if let url = model.createdURL { Text(url).font(.system(.body, design: .monospaced)) }
        Button("Copy") { model.copyCreatedLink() }
        if let message = model.copyMessage { Text(message) }
        Button("Done") { model.dismissCreatedLink() }
      }.padding(24).frame(width: 540)
    }.sheet(item: Binding(get: { model.revoking }, set: { value in
      if value == nil { model.cancelRevoke() }
    })) { link in
      VStack(alignment: .leading, spacing: 16) {
        Text("Revoke \(link.name)?").font(.title2)
        Text("The link stops working immediately.")
        Button("Revoke", role: .destructive) { Task { await model.confirmRevoke() } }
        Button("Cancel", role: .cancel) { model.cancelRevoke() }
      }.padding(24).frame(width: 480)
    }.task {
      while !Task.isCancelled {
        await model.load()
        do { try await Task.sleep(for: .seconds(15)) } catch { return }
      }
    }
  }
}
