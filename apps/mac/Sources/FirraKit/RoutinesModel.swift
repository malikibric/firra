import Foundation
import Observation

/// Server-confirmed settings. Writes are serialized and never queued or cached.
@MainActor @Observable
public final class RoutinesModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var items: [RoutineItem] = []
  public private(set) var workspaceId: String?
  public private(set) var isStale = false
  public private(set) var isLoading = false
  public private(set) var saving: RoutineKind?
  public private(set) var message: String?
  public var canEdit: Bool { phase == .ready && !isStale && !isLoading && saving == nil }

  private let api: APIClient
  private var workspace = UUID()
  private var request = UUID()

  public init(api: APIClient) { self.api = api }

  public func load(workspaceId: String) async {
    if self.workspaceId != workspaceId {
      workspace = UUID()
      self.workspaceId = workspaceId
      items = []
      message = nil
      isStale = false
    } else if saving != nil {
      return
    }
    await refresh(workspaceId: workspaceId)
  }

  public func setEnabled(workspaceId: String, kind: RoutineKind, enabled: Bool) async {
    guard self.workspaceId == workspaceId, canEdit,
      let item = items.first(where: { $0.kind == kind }), item.enabled != enabled
    else { return }
    let workspace = self.workspace
    saving = kind
    message = nil
    defer { saving = nil }
    do {
      try await api.setRoutineEnabled(workspaceId: workspaceId, kind: kind, enabled: enabled)
      guard self.workspace == workspace else { return }
      if let index = items.firstIndex(where: { $0.kind == kind }) { items[index].enabled = enabled }
      await refresh(workspaceId: workspaceId)
    } catch {
      guard self.workspace == workspace else { return }
      message = Self.text(error)
    }
  }

  private func refresh(workspaceId: String) async {
    let request = UUID()
    self.request = request
    isLoading = true
    if items.isEmpty { phase = .loading }
    defer { if self.request == request { isLoading = false } }
    do {
      let response = try await api.routines(workspaceId: workspaceId)
      guard self.request == request else { return }
      items = response.items
      phase = .ready
      isStale = false
      message = nil
    } catch {
      guard self.request == request else { return }
      if items.isEmpty {
        phase = .failed(Self.text(error))
      } else {
        phase = .ready
        isStale = true
        message = Self.text(error)
      }
    }
  }

  private static func text(_ error: Error) -> String {
    if case APIError.server(let status, _, _, _) = error, status == 403 {
      return "You do not have access to these routine settings."
    }
    return (error as? APIError)?.userMessage ?? "Could not load routines. Try again."
  }
}
