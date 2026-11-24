import Foundation
import Observation

/// The status route owns connection state; no mailbox bodies are requested by the app.
public struct LocalStatus: Decodable, Sendable, Equatable {
  public struct Connection: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let provider: String
    public let accountLabel: String
    public let state: String
    public let lastSyncedAt: String?
    public let errorClass: String?
  }
  public let engineVersion: String
  public let ai: LocalAIStatus?
  public let database: String
  public let connections: [Connection]
}

@MainActor @Observable
public final class ConnectionsModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var connections: [LocalStatus.Connection] = []
  public private(set) var message: String?
  private let api: APIClient
  private let reconnectAccount: @MainActor (LocalStatus.Connection) async throws -> Void

  public init(api: APIClient,
    reconnect: @escaping @MainActor (LocalStatus.Connection) async throws -> Void) {
    self.api = api
    self.reconnectAccount = reconnect
  }

  public func load(workspaceId: String) async {
    if connections.isEmpty { phase = .loading }
    do {
      connections = try await api.localStatus().connections.filter {
        ["gmail", "google_calendar"].contains($0.provider)
      }
      phase = .ready
    } catch {
      phase = connections.isEmpty ? .failed(Self.text(error)) : .ready
      message = Self.text(error)
    }
  }

  public func reconnect(workspaceId: String, connection: LocalStatus.Connection) async {
    do {
      try await reconnectAccount(connection)
      message = nil
    } catch GoogleSignInError.cancelled { return }
    catch { message = Self.text(error) }
    await load(workspaceId: workspaceId)
  }

  public func disconnect(workspaceId: String, connectionId: String) async {
    do {
      try await api.disconnect(workspaceId: workspaceId, connectionId: connectionId)
      message = nil
    } catch { message = Self.text(error) }
    await load(workspaceId: workspaceId)
  }

  private static func text(_ error: Error) -> String {
    (error as? GoogleOAuthConfigError)?.userMessage
      ?? (error as? APIError)?.userMessage ?? "The Google account could not be connected."
  }
}
