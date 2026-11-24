import FirraKit
import SwiftUI

struct OnboardingView: View {
  let model: OnboardingModel
  let connectAI: () -> Void
  let connectSlack: () -> Void
  var body: some View {
    VStack(alignment: .leading, spacing: 20) {
      Text("Set up Firra").font(.largeTitle)
      switch model.step {
      case .welcome: WelcomeSetupView(model: model.welcome, next: model.advance)
      case .google: GoogleSetupView(model: model.google, next: model.advance)
      case .clients:
        ClientsSetupView(model: model.clients, connection: model.google.connectionId ?? "", next: model.advance)
      case .ai:
        ScrollView {
          VStack(alignment: .leading, spacing: 20) {
            GroupBox("Optional: connect Slack") {
              VStack(alignment: .leading) {
                Text("Map Slack channels to your chosen clients. You can also do this later in Settings.")
                Button("Connect Slack", action: connectSlack)
                Text("Skip by continuing with AI setup below.").font(.caption)
              }
            }
            AISetupView(model: model.ai, months: model.google.backfillMonths, next: model.advance, sourceAccess: model.sourceAccess)
          }
        }
      case .results: FirstResultsView(model: model.results, finish: model.finish, connectAI: connectAI)
      case .done: EmptyView()
      }
    }
    .padding(40).frame(maxWidth: 680, maxHeight: .infinity, alignment: .topLeading)
  }
}

private struct WelcomeSetupView: View {
  @Bindable var model: WelcomeSetupModel
  let next: () -> Void
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Text("Mail and Slack text stay on this Mac. Only the AI tool you choose sees text.")
      Text("Consumer AI plans may train on conversations unless you switch that off in your AI tool.")
      Text("Firra never sends anything to clients.")
      if let warning = model.warning { Text(warning).foregroundStyle(.orange) }
      Button("Continue") { model.acknowledged = true; next() }
    }.task { await model.check { await EngineSupervisor.fileVaultStatus() } }
  }
}

private struct GoogleSetupView: View {
  @Bindable var model: GoogleSetupModel
  let next: () -> Void
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Text("Connect Google").font(.title2)
      Picker("Read mail from the last", selection: $model.backfillMonths) {
        ForEach([3, 6, 12], id: \.self) { Text("\($0) months").tag($0) }
      }.disabled(model.busy)
      Text("Google opens in your browser. Firra requests read access to Gmail and Calendar.")
      if let message = model.message { Text(message) }
      Button(model.busy ? "Connecting…" : "Connect Google") { Task { await model.connect() } }.disabled(model.busy)
      if model.connectionId != nil { Button("Continue", action: next) }
    }
  }
}

private struct ClientsSetupView: View {
  @Bindable var model: ClientsSetupModel
  @State private var suggesting = 0
  let connection: String
  let next: () -> Void
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Text("Which clients should Firra follow?").font(.title2)
      Text("Type each client's name and the email domain they write from, for example acme.com. Firra reads only mail to and from these domains and ignores everything else.")
      HStack {
        TextField("Client name", text: $model.newName)
        TextField("Email domain (acme.com)", text: $model.newDomain)
          .onSubmit { model.addClient() }
        Button("Add") { model.addClient() }
          .disabled(model.newDomain.trimmingCharacters(in: .whitespaces).isEmpty)
      }
      ScrollView {
        ForEach($model.choices) { $choice in
          VStack(alignment: .leading) {
            Toggle(choice.threadCount > 0 ? "\(choice.domain) · \(choice.threadCount) threads" : choice.domain, isOn: $choice.selected)
            if !choice.sampleNames.isEmpty { Text(choice.sampleNames.joined(separator: ", ")).font(.caption) }
            if choice.selected {
              TextField("Client name", text: $choice.name)
              Picker("Mail policy", selection: $choice.policy) {
                Text("Full").tag(ClientPolicy.full)
                Text("Metadata only").tag(ClientPolicy.metadataOnly)
                Text("Excluded").tag(ClientPolicy.excluded)
              }
              if choice.policy == .excluded {
                Toggle("I confirm: exclude this client and delete its stored data.", isOn: $choice.confirmedExclusion)
              }
            }
          }.padding(.bottom)
        }
      }
      if model.state == "running" { ProgressView(model.scanned > 0 ? "Reading mail headers… \(model.scanned) read" : "Reading mail headers…") }
      if model.state == "none" || model.state == "failed" {
        VStack(alignment: .leading, spacing: 4) {
          Button("Suggest domains from my mail (optional)") { model.reset(); suggesting += 1 }
            .disabled(model.busy)
          Text("Reads the sender and recipient addresses of your recent mail to rank domains. Nothing is stored.").font(.caption)
        }
      }
      if let message = model.message { Text(message) }
      Button(model.saved ? "Continue" : "Save clients") {
        Task { if model.saved { next() } else { await model.saveClients() } }
      }.disabled(model.busy || (!model.saved && !model.canSave))
    }.task(id: suggesting) {
      guard suggesting > 0 else { return }
      while !Task.isCancelled && model.state != "ready" && model.state != "failed" {
        await model.poll(connectionId: connection, start: model.state == "none")
        if model.state == "ready" || model.state == "failed" { break }
        try? await Task.sleep(for: .seconds(2))
      }
    }
  }
}

struct AISetupView: View {
  @Environment(NotificationFocus.self) private var notificationFocus
  @Bindable var model: AISetupModel
  let months: Int
  let next: () -> Void
  var settings = false
  var sourceAccess: SourceAccessModel? = nil
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Text("Choose AI processing").font(.title2)
      if let tool = model.status?.tools.first(where: { $0.tool == "claude_code" }) {
        Text("Claude Code: \(tool.installed ? "installed" : "not installed") · \(tool.signedIn == true ? "signed in" : "sign-in needed")")
      }
      Text("Codex background processing is not supported yet.")
      Picker("Processing", selection: $model.background) {
        Text("Only in conversation — I will ask my AI to process mail").tag(false)
        Text("Background with Claude Code").tag(true)
      }
      if model.background {
        Toggle("Uses my own AI subscription and may count against its limits", isOn: $model.acknowledged)
      }
      if let sourceAccess { SourceAccessSwitch(model: sourceAccess) }
      Text(model.waiting)
      if let message = model.message { Text(message) }
      Button(settings ? "Save AI settings" : "Continue") {
        Task { await model.save(backfillMonths: settings ? model.backfillMonths : months); if model.saved { next() } }
      }
        .disabled(model.busy || model.background && !model.acknowledged)
    }.onAppear { notificationFocus.actionKinds.insert("claude") }
      .onDisappear { notificationFocus.actionKinds.remove("claude") }
      .task { if settings { await model.loadSettings() } else { await model.load() } }
  }
}

private struct FirstResultsView: View {
  let model: FirstResultsModel
  let finish: () -> Void
  let connectAI: () -> Void
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Text("First results").font(.title2)
      ProgressView()
      Text(model.message)
      Button("Connect AI tools", action: connectAI)
      Button("Open Today", action: finish)
    }
    .task {
      while !Task.isCancelled && !model.finished {
        await model.poll()
        try? await Task.sleep(for: .seconds(2))
      }
    }
    .task {
      do { try await Task.sleep(for: .seconds(60)); if !Task.isCancelled { finish() } } catch {}
    }
    .onChange(of: model.finished) { _, finished in if finished { finish() } }
  }
}
