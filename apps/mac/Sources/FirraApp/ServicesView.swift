import FirraKit
import SwiftUI

/// The firm's service catalog: what Firra matches client needs against.
struct ServicesView: View {
  let model: ServicesModel
  let workspaceId: String
  @State private var adding = false
  @State private var editing: ServiceItem?
  @State private var deleting: ServiceItem?

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack {
        Text("Services").font(.title2.bold())
        Spacer()
        Button("Add service…") { adding = true }
      }
      Text("Firra matches recurring client needs against these services.")
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
        List(model.services) { service in
          HStack {
            VStack(alignment: .leading, spacing: 2) {
              Text(service.name)
              Text(service.keywords.isEmpty ? "No keywords yet" : service.keywords.joined(separator: ", "))
                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            Button("Edit") { editing = service }
            Button("Delete", role: .destructive) { deleting = service }
          }
        }
        .overlay {
          if model.services.isEmpty {
            ContentUnavailableView(
              "No services yet", systemImage: "briefcase",
              description: Text("Add the services your firm sells, with matching keywords."))
          }
        }
      }
    }
    .padding()
    .task(id: workspaceId) { await model.load(workspaceId: workspaceId) }
    .sheet(isPresented: $adding) { ServiceSheet(model: model, workspaceId: workspaceId, service: nil) }
    .sheet(item: $editing) { ServiceSheet(model: model, workspaceId: workspaceId, service: $0) }
    .confirmationDialog(
      "Delete this service?", isPresented: Binding(
        get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
      presenting: deleting
    ) { service in
      Button("Delete", role: .destructive) {
        Task { await model.delete(workspaceId: workspaceId, serviceId: service.id) }
      }
      Button("Cancel", role: .cancel) {}
    } message: { _ in Text("Firra stops matching client needs against it.") }
  }
}

private struct ServiceSheet: View {
  let model: ServicesModel
  let workspaceId: String
  let service: ServiceItem?
  @Environment(\.dismiss) private var dismiss
  @State private var name: String
  @State private var description: String
  @State private var keywords: String

  init(model: ServicesModel, workspaceId: String, service: ServiceItem?) {
    self.model = model
    self.workspaceId = workspaceId
    self.service = service
    _name = State(initialValue: service?.name ?? "")
    _description = State(initialValue: service?.description ?? "")
    _keywords = State(initialValue: service?.keywords.joined(separator: ", ") ?? "")
  }

  var body: some View {
    Form {
      TextField("Name", text: $name)
      TextField("Description", text: $description, prompt: Text("What this service covers"))
      TextField("Keywords (comma separated)", text: $keywords, prompt: Text("seo, paid ads"))
      HStack {
        Button("Cancel") { dismiss() }
        Spacer()
        Button(service == nil ? "Add" : "Save") {
          Task {
            if await model.save(
              workspaceId: workspaceId, serviceId: service?.id, name: name,
              description: description, keywords: keywords) { dismiss() }
          }
        }
        .keyboardShortcut(.defaultAction)
        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
      }
      if let message = model.message { Text(message).foregroundStyle(.red) }
    }
    .formStyle(.grouped).padding().frame(width: 460)
  }
}
