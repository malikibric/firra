import Foundation
import Observation
import OSLog

/// One owner and one API client per engine handshake. Late responses from retired engines are ignored.
@MainActor @Observable
public final class LocalEngineModel {
  public private(set) var state: EngineState = .stopped
  public private(set) var api: APIClient?
  public private(set) var me: MeResponse?
  /// The most recent start-up problem (safe text), kept while the supervisor retries so the screen can say why.
  public private(set) var lastFailure: String?
  public var workspaceId: String? { me?.memberships.first?.workspaceId }
  private var endpoint: EngineEndpoint?
  private let transport: any HTTPTransport
  private let restartEngine: @Sendable () async -> Void

  public init(transport: any HTTPTransport = URLSession.shared,
              restart: @escaping @Sendable () async -> Void) {
    self.transport = transport
    self.restartEngine = restart
  }

  public func receive(_ state: EngineState) async {
    if case .failed(let text) = state { lastFailure = text }
    guard case .running(let ready) = state else {
      self.state = state
      endpoint = nil
      api = nil
      me = nil
      return
    }
    guard endpoint != ready else { return }
    endpoint = ready
    self.state = .starting
    me = nil
    let client = APIClient(base: ready.baseURL, transport: transport,
      tokenProvider: { ready.token }, onUnauthorized: { [weak self] in
        await self?.unauthorized(ready)
      })
    api = client
    do {
      let owner = try await client.me()
      guard endpoint == ready else { return }
      guard owner.memberships.count == 1, owner.memberships[0].role == "owner" else {
        throw APIError.transport("local owner missing")
      }
      me = owner
      lastFailure = nil
      self.state = state
    } catch {
      guard endpoint == ready else { return }
      api = nil
      me = nil
      self.state = .failed("The local owner could not be loaded.")
    }
  }

  private func unauthorized(_ ready: EngineEndpoint) async {
    guard endpoint == ready else { return }
    Logger(subsystem: "app.firra.mac", category: "engine").notice("Engine handshake restarting")
    endpoint = nil
    api = nil
    me = nil
    state = .restarting(attempt: 1)
    await restartEngine()
  }

  /// Retire authentication callbacks before an explicit wipe; a late 401 must not restart the engine.
  public func suspendForDeletion() { endpoint = nil; api = nil; me = nil }

  public func restart() async {
    endpoint = nil
    api = nil
    me = nil
    state = .starting
    await restartEngine()
  }
}
