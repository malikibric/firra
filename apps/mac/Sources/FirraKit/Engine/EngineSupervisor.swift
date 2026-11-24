import Foundation

public enum EngineState: Sendable, Equatable {
  case stopped
  case installing
  case starting
  case running(EngineEndpoint)
  case restarting(attempt: Int)
  case failed(String)
}

/// Things the engine tells the app on the control socket outside any request.
public enum EngineEvent: Sendable, Equatable {
  case needReauth(connectionID: String)
  case syncStateChanged
  case aiStatusChanged
  case relayStateChanged
}

public struct EngineTiming: Sendable {
  public var connectTimeout: TimeInterval = 30
  public var handshakeTimeout: TimeInterval = 120
  public var pingInterval: TimeInterval = 10
  public var pongTimeout: TimeInterval = 5
  public var maxMissedPongs = 3
  public var shutdownGrace: TimeInterval = 20
  public var killAfter: TimeInterval = 5
  public var backoff: @Sendable (Int) -> TimeInterval = { min(pow(2, Double($0)), 30) }
  public init() {}
}

/// Starts the engine, hands it its secrets, keeps it alive and stops it (docs/plans/local/contracts.md section 2).
/// The app never writes a secret to disk outside the Keychain and never puts one on a command line or in the
/// engine's environment: they go over the control socket only.
public actor EngineSupervisor {
  private let home: URL
  private let controlDirectory: URL
  private let appVersion: String
  private let installer: any EngineInstalling
  private let launcher: any EngineLaunching
  private let secrets: any EngineSecrets
  private let timing: EngineTiming
  private let onEvent: @Sendable (EngineEvent) -> Void

  private var loop: Task<Void, Never>?
  private var stopping = false
  private var channel: ControlChannel?
  private var process: (any RunningEngine)?
  private var processExited = false
  private var readerEnded = false
  private var lastPong = Date.distantPast
  private var state: EngineState = .stopped
  private var continuations: [UUID: AsyncStream<EngineState>.Continuation] = [:]

  public init(
    home: URL, controlDirectory: URL, appVersion: String, installer: any EngineInstalling,
    launcher: any EngineLaunching = ProcessEngineLauncher(), secrets: any EngineSecrets,
    timing: EngineTiming = EngineTiming(), onEvent: @escaping @Sendable (EngineEvent) -> Void = { _ in }
  ) {
    self.home = home
    self.controlDirectory = controlDirectory
    self.appVersion = appVersion
    self.installer = installer
    self.launcher = launcher
    self.secrets = secrets
    self.timing = timing
    self.onEvent = onEvent
  }

  /// A unix socket path is limited to about 100 bytes; fall back to a private directory in /tmp when the
  /// Application Support path is too long (same rule as the engine's database supervisor).
  public static func controlDirectory(home: URL) throws -> URL {
    let preferred = home.appending(path: "run")
    if Array((preferred.path + "/control.sock").utf8).count <= 100 { return preferred }
    return URL(fileURLWithPath: "/tmp/firra-\(getuid())")
  }

  // MARK: - public surface

  public var endpoint: EngineEndpoint? {
    if case .running(let endpoint) = state { return endpoint }
    return nil
  }

  public func states() -> AsyncStream<EngineState> {
    let id = UUID()
    return AsyncStream { continuation in
      continuations[id] = continuation
      continuation.yield(state)
      continuation.onTermination = { [weak self] _ in
        Task { await self?.removeContinuation(id) }
      }
    }
  }

  public func start() {
    guard loop == nil else { return }
    stopping = false
    loop = Task { [weak self] in await self?.supervise() }
  }

  /// Orderly stop: `shutdown` over the control socket, then SIGTERM and SIGKILL if the engine lingers.
  public func stop() async {
    stopping = true
    loop?.cancel()
    loop = nil
    await shutDownEngine()
    set(.stopped)
  }

  /// A secret changed (owner connected or disconnected an account): store it and tell the running engine.
  public func putSecret(_ value: String, for name: String) async throws {
    guard SecretSlot.isValid(name) else { throw EngineError.protocolViolation("unknown secret slot") }
    try secrets.set(value, for: name)
    try channel?.send(.secretSet(name: name, value: value))
  }

  public func deleteSecret(_ name: String) async throws {
    try secrets.delete(name)
    try channel?.send(.secretDelete(name: name))
  }

  // MARK: - supervision

  private func removeContinuation(_ id: UUID) { continuations.removeValue(forKey: id) }

  private func set(_ new: EngineState) {
    state = new
    for continuation in continuations.values { continuation.yield(new) }
  }

  private func supervise() async {
    var attempt = 0
    while !Task.isCancelled && !stopping {
      do {
        set(.installing)
        let installer = self.installer
        let layout = try await Task.detached { try installer.install() }.value
        set(.starting)
        let endpoint = try await bringUp(layout)
        attempt = 0
        set(.running(endpoint))
        await monitor()
        if stopping { break }
      } catch {
        if stopping { break }
        set(.failed(Self.describe(error)))
      }
      await shutDownEngine()
      if stopping { break }
      attempt += 1
      set(.restarting(attempt: attempt))
      try? await Task.sleep(for: .seconds(timing.backoff(attempt)))
    }
  }

  public nonisolated static func describe(_ error: Error) -> String {
    switch error as? EngineError {
    case .some(.timeout(let what)): return "timeout: \(what)"
    case .some(.refused(let code)): return "engine refused: \(code)"
    case .some(.installFailed(let what)): return "install failed: \(what)"
    case .some(.launchFailed(let what)): return "launch failed: \(what)"
    case .some(.socket(let what)): return "socket: \(what)"
    case .some(.protocolViolation(let what)): return "protocol: \(what)"
    case .none: return "engine error"
    }
  }

  private func bringUp(_ layout: EngineLayout) async throws -> EngineEndpoint {
    try FileManager.default.createDirectory(
      at: home, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    try FileManager.default.createDirectory(
      at: controlDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    processExited = false
    readerEnded = false
    lastPong = Date()
    let running = try launcher.launch(
      EngineLaunchRequest(layout: layout, home: home, controlDirectory: controlDirectory))
    process = running
    Task { [weak self] in
      await running.waitUntilExit()
      await self?.markProcessExited()
    }
    let socketPath = controlDirectory.appending(path: "control.sock").path
    let timing = self.timing
    let version = appVersion
    let secrets = self.secrets
    let result: (ControlChannel, EngineEndpoint)
    do {
      result = try await Task.detached { () throws -> (ControlChannel, EngineEndpoint) in
      let channel = try ControlChannel.connect(path: socketPath, timeout: timing.connectTimeout)
      do {
        try channel.send(.hello(appVersion: version))
        guard let ack = try channel.receive(timeout: timing.handshakeTimeout) else {
          throw EngineError.socket("engine closed during hello")
        }
        if ack.type == "error" { throw EngineError.refused(ack.code ?? "unknown") }
        guard ack.type == "hello_ack", ack.protocolVersion == 1 else {
          throw EngineError.protocolViolation("expected hello_ack")
        }
        for (name, value) in try secrets.all().sorted(by: { $0.key < $1.key }) {
          try channel.send(.secretSet(name: name, value: value))
        }
        try channel.send(.secretsComplete)
        guard let ready = try channel.receive(timeout: timing.handshakeTimeout) else {
          throw EngineError.socket("engine closed before ready")
        }
        if ready.type == "error" { throw EngineError.refused(ready.code ?? "unknown") }
        guard ready.type == "ready", let port = ready.apiPort, let token = ready.apiToken else {
          throw EngineError.protocolViolation("expected ready")
        }
        return (channel, EngineEndpoint(port: port, token: token))
      } catch {
        channel.close()
        throw error
      }
      }.value
    } catch {
      // An engine that is already gone explains the timeout better than the timeout does.
      if processExited { throw EngineError.launchFailed("the engine exited right after it started (see log/engine-stderr.log)") }
      throw error
    }
    let (channel, endpoint) = result
    self.channel = channel
    Task.detached { [weak self] in await self?.readLoop(channel) }
    return endpoint
  }

  private func markProcessExited() { processExited = true }
  private func markReaderEnded() { readerEnded = true }
  private func markPong() { lastPong = Date() }

  /// Reads engine -> app messages until the connection ends.
  private func readLoop(_ channel: ControlChannel) async {
    while true {
      let message: HandshakeMessage?
      do {
        message = try await Task.detached { try channel.receive(timeout: 1) }.value
      } catch EngineError.timeout {
        if await isStopped(channel) { return }
        continue
      } catch {
        markReaderEnded()
        return
      }
      guard let message else {
        markReaderEnded()
        return
      }
      handle(message)
    }
  }

  private func isStopped(_ candidate: ControlChannel) -> Bool { channel !== candidate }

  private func handle(_ message: HandshakeMessage) {
    switch message.type {
    case "pong": markPong()
    case "event":
      switch message.event {
      case "need_reauth": if let id = message.connectionId { onEvent(.needReauth(connectionID: id)) }
      case "sync_state_changed": onEvent(.syncStateChanged)
      case "ai_status_changed": onEvent(.aiStatusChanged)
      case "relay_state_changed": onEvent(.relayStateChanged)
      default: break
      }
    case "secret_delete":
      if let name = message.name, SecretSlot.isValid(name) { try? secrets.delete(name) }
    case "secret_set":
      // A rotated token from the engine: keep the Keychain current.
      if let name = message.name, let value = message.value, SecretSlot.isValid(name) {
        try? secrets.set(value, for: name)
      }
    default: break
    }
  }

  /// Returns when the engine needs to be restarted (or the supervisor is stopping).
  private func monitor() async {
    var missed = 0
    while !Task.isCancelled && !stopping {
      try? await Task.sleep(for: .seconds(timing.pingInterval))
      if stopping || Task.isCancelled { return }
      if processExited || readerEnded { return }
      let sent = Date()
      do { try channel?.send(.ping) } catch { return }
      try? await Task.sleep(for: .seconds(timing.pongTimeout))
      if lastPong >= sent { missed = 0 } else { missed += 1 }
      if missed >= timing.maxMissedPongs { return }
    }
  }

  private func shutDownEngine() async {
    let channel = self.channel
    let running = self.process
    self.channel = nil
    self.process = nil
    try? channel?.send(.shutdown)
    if let running {
      let grace = timing.shutdownGrace
      let exited = await Self.waitForExit(running, within: grace)
      if !exited {
        running.terminate()
        if await !Self.waitForExit(running, within: timing.killAfter) { running.kill() }
      }
    }
    channel?.close()
  }

  /// Waits for the process to exit, or gives up after `seconds`. The losing side is never joined: a process that
  /// ignores `shutdown` must not keep the caller from escalating to SIGTERM and SIGKILL.
  private static func waitForExit(_ process: any RunningEngine, within seconds: TimeInterval) async -> Bool {
    let race = Race()
    return await withCheckedContinuation { continuation in
      Task.detached {
        await process.waitUntilExit()
        if await race.claim() { continuation.resume(returning: true) }
      }
      Task.detached {
        try? await Task.sleep(for: .seconds(seconds))
        if await race.claim() { continuation.resume(returning: false) }
      }
    }
  }
}

/// First caller wins; used to resume a continuation exactly once.
private actor Race {
  private var claimed = false
  func claim() -> Bool {
    if claimed { return false }
    claimed = true
    return true
  }
}
