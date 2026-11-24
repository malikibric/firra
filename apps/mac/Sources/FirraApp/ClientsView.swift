import FirraKit
import SwiftUI

/// Clients, the domains that map mail to them, and the data policy of each.
struct ClientsView: View {
  let model: ClientsModel
  let detail: ClientDetailModel
  let workspaceId: String
  @State private var adding = false
  @State private var editing: SetupClient?
  @State private var viewing: SetupClient?

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack {
        Text("Clients").font(.title2.bold())
        Spacer()
        Button("Add client…") { adding = true }
      }
      Text("Only mail that involves a client below is kept. Everything else is discarded when it arrives.")
        .foregroundStyle(.secondary)
      if let message = model.message { Text(message).foregroundStyle(.red) }
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
        List(model.clients) { client in
          HStack {
            Button { editing = client } label: { row(client) }.buttonStyle(.plain)
            if client.policy != .excluded {
              Button("Details") { viewing = client }.buttonStyle(.link)
            }
          }
        }
        .overlay {
          if model.clients.isEmpty {
            ContentUnavailableView(
              "No clients yet", systemImage: "person.2",
              description: Text("Add the clients Firra should watch, with their email domains."))
          }
        }
      }
    }
    .padding()
    .task(id: workspaceId) { await model.load(workspaceId: workspaceId) }
    .sheet(isPresented: $adding) { AddClientSheet(model: model, workspaceId: workspaceId) }
    .sheet(item: $editing) { EditClientSheet(model: model, workspaceId: workspaceId, client: $0) }
    .sheet(item: $viewing) { client in
      VStack(spacing: 0) {
        HStack { Spacer(); Button("Close") { viewing = nil }.keyboardShortcut(.cancelAction) }.padding([.top, .horizontal])
        ClientDetailView(model: detail, workspaceId: workspaceId, clientId: client.id, name: client.name)
      }
      .frame(minWidth: 620, minHeight: 560)
    }
  }

  private func row(_ client: SetupClient) -> some View {
    HStack {
      VStack(alignment: .leading, spacing: 2) {
        Text(client.name)
        Text(client.domains.isEmpty ? "No domains yet" : client.domains.joined(separator: ", "))
          .font(.caption).foregroundStyle(.secondary).lineLimit(1)
      }
      Spacer()
      Text(client.policy.label).font(.caption)
        .padding(.horizontal, 8).padding(.vertical, 2)
        .background(.quaternary, in: Capsule())
    }
    .contentShape(Rectangle())
  }
}

private struct AddClientSheet: View {
  let model: ClientsModel
  let workspaceId: String
  @Environment(\.dismiss) private var dismiss
  @State private var name = ""
  @State private var domains = ""
  @State private var policy = ClientPolicy.full
  @State private var confirmExclusion = false

  var body: some View {
    Form {
      TextField("Name", text: $name)
      TextField("Domains (comma separated)", text: $domains, prompt: Text("acme.com, acme.co.uk"))
      PolicyPicker(policy: $policy, confirmExclusion: $confirmExclusion)
      HStack {
        Button("Cancel") { dismiss() }
        Spacer()
        Button("Add") {
          Task {
            if await model.add(workspaceId: workspaceId, name: name, domains: domains, policy: policy) { dismiss() }
          }
        }
        .keyboardShortcut(.defaultAction)
        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || (policy == .excluded && !confirmExclusion))
      }
      if let message = model.message { Text(message).foregroundStyle(.red) }
    }
    .formStyle(.grouped).padding().frame(width: 460)
  }
}

private struct EditClientSheet: View {
  let model: ClientsModel
  let workspaceId: String
  let client: SetupClient
  @Environment(\.dismiss) private var dismiss
  @State private var name: String
  @State private var domains: String
  @State private var policy: ClientPolicy
  @State private var confirmExclusion = false

  init(model: ClientsModel, workspaceId: String, client: SetupClient) {
    self.model = model
    self.workspaceId = workspaceId
    self.client = client
    _name = State(initialValue: client.name)
    _domains = State(initialValue: client.domains.joined(separator: ", "))
    _policy = State(initialValue: client.policy)
  }

  var body: some View {
    Form {
      TextField("Name", text: $name)
      TextField("Domains (comma separated)", text: $domains)
      PolicyPicker(policy: $policy, confirmExclusion: $confirmExclusion)
      if policy != client.policy && policy != .full {
        Text("Switching to \(policy.label) removes stored text for this client now.")
          .font(.callout).foregroundStyle(.orange)
      }
      HStack {
        Button("Cancel") { dismiss() }
        Spacer()
        Button("Save") {
          Task {
            var ok = await model.rename(workspaceId: workspaceId, client: client, to: name)
            if ok && ClientsModel.parseDomains(domains) != client.domains {
              ok = await model.setDomains(workspaceId: workspaceId, clientId: client.id, domains: domains)
            }
            if ok && policy != client.policy {
              ok = await model.setPolicy(workspaceId: workspaceId, clientId: client.id, policy: policy)
            }
            if ok { dismiss() }
          }
        }
        .keyboardShortcut(.defaultAction)
        .disabled(policy == .excluded && policy != client.policy && !confirmExclusion)
      }
      if let message = model.message { Text(message).foregroundStyle(.red) }
    }
    .formStyle(.grouped).padding().frame(width: 460)
  }
}

private struct PolicyPicker: View {
  @Binding var policy: ClientPolicy
  @Binding var confirmExclusion: Bool

  var body: some View {
    Picker("Data policy", selection: $policy) {
      ForEach(ClientPolicy.allCases, id: \.self) { Text($0.label).tag($0) }
    }
    Text(policy.explanation).font(.caption).foregroundStyle(.secondary)
    if policy == .excluded {
      Toggle("I understand this deletes what Firra holds about this client", isOn: $confirmExclusion)
    }
  }
}
