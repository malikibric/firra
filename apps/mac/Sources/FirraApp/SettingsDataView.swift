import AppKit
import FirraKit
import SwiftUI
import UniformTypeIdentifiers

@MainActor enum SettingsPanels {
  static func save(_ filename: String) async -> URL? {
    let panel = NSSavePanel()
    panel.nameFieldStringValue = filename
    panel.canCreateDirectories = true
    return await withCheckedContinuation { continuation in
      panel.begin { result in continuation.resume(returning: result == .OK ? panel.url : nil) }
    }
  }
  static func openBackup() async -> URL? {
    let panel = NSOpenPanel()
    panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
    panel.allowedContentTypes = [UTType(filenameExtension: "firrabackup") ?? .data]
    return await withCheckedContinuation { continuation in
      panel.begin { result in continuation.resume(returning: result == .OK ? panel.url : nil) }
    }
  }
}

struct SettingsDataView: View {
  @Bindable var model: SettingsDataModel
  let ai: AISetupModel?
  var updates: UpdateModel?
  var loginItem: LoginItemModel?
  var connectAI: () -> Void = {}
  var connectSlack: () -> Void = {}
  var runSetup: () -> Void = {}
  @State private var showingPhrase = false
  @State private var showingRestore = false
  @State private var showingDelete = false
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 20) {
        Text("Data and privacy").font(.title2)
        if model.deletionStarted {
          Text(model.remaining)
          if let error = model.deleteError { Text(error).foregroundStyle(.red) }
          Button("Retry deletion") { Task { await model.deleteEverything() } }.disabled(model.busy)
        } else {
          Button("Connect AI tools", action: connectAI)
          Button("Connect Slack", action: connectSlack)
          Button("Run setup again", action: runSetup)
          Divider()
          Picker("Keep the text of mail, Slack and meetings for:", selection: Binding(
            get: { model.bodyMonths }, set: { months in Task { await model.requestRetention(months) } })) {
            Text("3 months").tag(Optional(3))
            Text("6 months").tag(Optional(6))
            Text("12 months (default)").tag(Optional(12))
            Text("24 months").tag(Optional(24))
            Text("Until I delete it").tag(Int?.none)
            if let months = model.bodyMonths, ![3, 6, 12, 24].contains(months) {
              Text("\(months) months (current)").tag(Optional(months))
            }
          }.disabled(!model.retentionLoaded || model.busy || model.pendingRetentionMonths != nil)
          Text(SettingsDataModel.retentionExplanation).font(.caption)
          if model.retentionLoading { ProgressView("Loading retention…") }
          if model.retentionSaving { ProgressView("Saving retention…") }
          if let error = model.retentionError { Text(error).foregroundStyle(.orange) }
          if model.retentionError != nil || !model.retentionLoaded && !model.retentionLoading {
            Button("Retry retention") { Task { await model.loadRetention() } }.disabled(model.busy)
          }
          Divider()
          Button("Create backup") {
            Task { await model.createBackup(); showingPhrase = model.recoveryPhrase != nil }
          }.disabled(model.busy || model.backupAvailable)
          if model.backupAvailable && model.recoveryPhrase == nil && model.savedPhrase {
            Button("Save backup") { Task { await model.saveBackup() } }.disabled(model.busy)
            Button("Discard unsaved backup") { model.discardBackup() }
          }
          Button("Restore from backup") {
            Task { await model.chooseRestore(); showingRestore = model.restoreFile != nil }
          }.disabled(model.busy)
          Button("Export diagnostics") { Task { await model.exportDiagnostics() } }.disabled(model.busy)
          Text("Diagnostics contain versions, counts and error classes; no mail, names or addresses.").font(.caption)
          Button("Export metrics") { Task { await model.exportMetrics() } }.disabled(model.busy)
          Text("Metrics are counts of how useful Firra's items were, how early it warned and how many important items it raised; no client names or text.").font(.caption)
          if let ai {
            Divider()
            AISetupView(model: ai, months: ai.backfillMonths, next: {}, settings: true)
          }
          if let loginItem {
            Divider()
            Toggle("Start Firra when I log in", isOn: Binding(get: { loginItem.isOn }, set: { loginItem.set($0) }))
            if let message = loginItem.message { Text(message).font(.caption) }
          }
          if let updates, updates.enabled {
            Divider()
            Text("Updates").font(.headline)
            if let release = updates.available {
              Text("Firra \(release.version) is available.")
              if let notes = release.notes { Text(notes).font(.caption) }
              Link("Download", destination: release.url)
              if let hint = updates.verifyHint { Text(hint).font(.caption).textSelection(.enabled) }
            } else if let error = updates.error { Text(error).font(.caption) }
            else { Text("Firra is up to date.").font(.caption) }
          }
          Divider()
          Text("Delete everything").font(.headline)
          Button("Delete everything…", role: .destructive) { showingDelete = true }.disabled(model.busy)
          if let message = model.message { Text(message) }
        }
      }.padding().frame(maxWidth: .infinity, alignment: .leading)
    }
    .task { await model.loadRetention() }
    .sheet(isPresented: Binding(get: { model.pendingRetentionMonths != nil }, set: { presented in
      if !presented { model.cancelRetention() }
    })) {
      VStack(alignment: .leading, spacing: 16) {
        Text("Keep text for \(model.pendingRetentionMonths ?? 12) months?").font(.title2)
        Text(SettingsDataModel.retentionExplanation)
        Button("Shorten retention", role: .destructive) { Task { await model.confirmRetention() } }
          .disabled(model.busy)
        Button("Cancel", role: .cancel) { model.cancelRetention() }
      }.padding(24).frame(width: 520)
    }
    .sheet(isPresented: $showingPhrase, onDismiss: {
      if model.savedPhrase { model.dismissPhrase() } else { model.discardBackup() }
    }) {
      VStack(alignment: .leading, spacing: 16) {
        Text("Your recovery phrase").font(.title2)
        Text("Write it down; Firra cannot recover it. This phrase is shown only once.")
        if let phrase = model.recoveryPhrase { Text(phrase).font(.system(.body, design: .monospaced)) }
        Button("Copy") { model.copyPhrase() }
        if let message = model.message { Text(message) }
        Toggle("I saved the phrase", isOn: $model.savedPhrase)
        Button("Choose where to save") {
          showingPhrase = false
          Task { await model.saveBackup() }
        }.disabled(!model.savedPhrase || model.busy)
        Button("Cancel") { model.discardBackup(); showingPhrase = false }
      }.padding(24).frame(width: 520)
    }
    .sheet(isPresented: $showingRestore, onDismiss: model.cancelRestore) {
      VStack(alignment: .leading, spacing: 16) {
        Text("Restore from backup").font(.title2)
        SecureField("Recovery phrase", text: $model.restorePhrase)
        if let message = model.message { Text(message) }
        Button("Restore") {
          Task { await model.restore(); if model.restoreFile == nil { showingRestore = false } }
        }.disabled(model.restorePhrase.isEmpty || model.busy)
        Button("Cancel") { showingRestore = false }.disabled(model.busy)
      }.padding(24).frame(width: 520)
    }
    .sheet(isPresented: $showingDelete) {
      VStack(alignment: .leading, spacing: 16) {
        Text("Delete everything on this Mac?").font(.title2)
        Text("Deletes all Firra data on this Mac, Google and Slack connections, and Firra keys in the Keychain.")
        Text("Your mail and Slack themselves are not deleted. The developer's Google OAuth client configuration is kept.")
        TextField("Type delete everything", text: $model.confirmation)
        if let error = model.deleteError { Text(error) }
        Button("Delete everything", role: .destructive) {
          showingDelete = false
          Task { await model.deleteEverything() }
        }.disabled(model.confirmation != "delete everything" || model.busy)
        Button("Cancel") { model.confirmation = ""; showingDelete = false }
      }.padding(24).frame(width: 520)
    }
    .onDisappear { model.cancelRetention(); model.discardBackup(); model.cancelRestore() }
  }
}
