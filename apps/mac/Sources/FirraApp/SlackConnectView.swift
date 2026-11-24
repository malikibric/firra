import AppKit
import FirraKit
import SwiftUI

struct SlackConnectView: View {
  @Bindable var model: SlackConnectModel
  @Environment(\.dismiss) private var dismiss
  @State private var token = ""
  @State private var label = ""

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack { Text("Connect Slack").font(.title); Spacer(); Button("Close") { token = ""; dismiss() } }
      Text("Firra only reads. It never posts.")
      if model.busy { ProgressView("Connecting to Slack and reading channel metadata…") }
      if let message = model.message { Text(message).foregroundStyle(.orange) }
      ScrollView {
        VStack(alignment: .leading, spacing: 16) {
          if model.mappingConnection != nil {
            Text("Map channels to clients").font(.headline)
            Text("Unmapped channels are never read. Direct messages are shown only with a mapped client.")
            Text("Review every channel before saving. On a new app session, this list starts from engine suggestions.").font(.caption)
            ForEach($model.choices) { $choice in
              VStack(alignment: .leading) {
                Text("\(choice.channel.label) · \(choice.channel.kind)")
                Text(choice.channel.reasonText).font(.caption)
                Picker("Client", selection: $choice.clientId) {
                  Text("Skip").tag(String?.none)
                  ForEach(model.clients) { client in Text(client.name).tag(Optional(client.id)) }
                }
                if choice.channel.needsColleagueConsent && choice.clientId != nil {
                  Toggle("I agree: messages of colleagues will be read", isOn: $choice.colleagueConsent)
                }
              }.padding(.bottom, 8)
            }
            Button("Save channel mappings") { Task { await model.save() } }
            Button("Back") { model.cancelMapping() }
          } else {
            ForEach(model.connections) { connection in
              GroupBox(connection.accountLabel) {
                VStack(alignment: .leading, spacing: 8) {
                  Text("State: \(connection.state)")
                  if let date = MenuStatusModel.syncDate(connection.lastSyncedAt) {
                    Text("Last sync: \(date.formatted())")
                  } else { Text("No sync yet") }
                  if connection.errorClass != nil { Text("Slack needs attention. Try syncing or reconnecting.") }
                  HStack {
                    Button("Sync now") { Task { await model.sync(connection.id) } }
                    Button("Remap channels") { Task { await model.remap(connection.id) } }
                    Button("Disconnect", role: .destructive) { model.disconnecting = connection.id }
                  }
                }.frame(maxWidth: .infinity, alignment: .leading)
              }
            }
            Text("1. Create your own Slack app from the Firra manifest.")
            HStack {
              Button("Open Slack to create the app") { NSWorkspace.shared.open(SlackManifest.creationURL) }
              Button("Copy manifest") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(SlackManifest.json, forType: .string)
              }
            }
            Text("2. In the app’s OAuth & Permissions page, install it to your workspace.")
            Text("3. Copy the User OAuth Token starting with xoxp- and paste it below.")
            TextField("Slack workspace name", text: $label)
            SecureField("User OAuth Token (xoxp-)", text: $token)
            Button("Connect workspace") {
              let pasted = token.trimmingCharacters(in: .whitespacesAndNewlines)
              token = ""
              Task { await model.connect(token: pasted, accountLabel: label) }
            }.disabled(token.isEmpty || label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
          }
        }
      }.disabled(model.busy)
    }.padding(24).frame(width: 680, height: 640)
      .task {
        while !Task.isCancelled {
          await model.load()
          do { try await Task.sleep(for: .seconds(15)) } catch { return }
        }
      }
      .onDisappear { token = "" }
      .alert("Disconnect Slack?", isPresented: Binding(
        get: { model.disconnecting != nil }, set: { if !$0 { model.disconnecting = nil } })) {
        Button("Disconnect", role: .destructive) { Task { await model.disconnect() } }
        Button("Cancel", role: .cancel) { model.disconnecting = nil }
      } message: { Text("Firra will stop reading this workspace and remove its token from Keychain.") }
  }
}
