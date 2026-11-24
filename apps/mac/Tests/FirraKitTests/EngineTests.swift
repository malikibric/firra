import Darwin
import Foundation
import Testing
@testable import FirraKit

// MARK: - a fake engine that speaks the control protocol

final class FakeEngine: @unchecked Sendable {
  enum Mode: Sendable { case healthy, silent, refuse(String) }
  let path: String
  let mode: Mode
  private let lock = NSLock()
  private var listenFD: Int32 = -1
  private var _received: [HandshakeMessage] = []
  private var channel: ControlChannel?
  private var running = true
  private let exitCondition = NSCondition()
  private var didExit = false

  init(directory: URL, mode: Mode) throws {
    self.mode = mode
    path = directory.appending(path: "control.sock").path
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    unlink(path)
    var addr = try ControlChannel.address(for: path)
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    let bound = withUnsafePointer(to: &addr) {
      $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
    }
    guard bound == 0, listen(fd, 1) == 0 else { throw EngineError.socket("fake bind failed") }
    listenFD = fd
    Thread.detachNewThread { [self] in serve() }
  }

  var received: [HandshakeMessage] { lock.withLock { _received } }

  func push(_ message: HandshakeMessage) { try? channel?.send(message) }

  func stop() {
    lock.withLock { running = false }
    if listenFD >= 0 { Darwin.close(listenFD); listenFD = -1 }
    channel?.close()
    unlink(path)
    exitCondition.lock()
    didExit = true
    exitCondition.broadcast()
    exitCondition.unlock()
  }

  /// Blocks until the fake process has exited; any number of callers may wait.
  func waitForExit() {
    exitCondition.lock()
    while !didExit { exitCondition.wait() }
    exitCondition.unlock()
  }

  private func serve() {
    let conn = accept(listenFD, nil, nil)
    guard conn >= 0 else { return }
    let ch = ControlChannel(fd: conn)
    lock.withLock { channel = ch }
    while lock.withLock({ running }) {
      guard let message = try? ch.receive(timeout: 0.1) else {
        if !lock.withLock({ running }) { return }
        continue
      }
      lock.withLock { _received.append(message) }
      switch message.type {
      case "hello":
        if case .refuse(let code) = mode {
          var error = HandshakeMessage(type: "error"); error.code = code
          try? ch.send(error)
          stop()
          return
        }
        var ack = HandshakeMessage(type: "hello_ack"); ack.protocolVersion = 1; ack.engineVersion = "t"
        try? ch.send(ack)
      case "secrets_complete":
        var ready = HandshakeMessage(type: "ready"); ready.apiPort = 4242
        ready.apiToken = String(repeating: "t", count: 40)
        try? ch.send(ready)
      case "ping":
        if case .healthy = mode { try? ch.send(HandshakeMessage(type: "pong")) }
      case "shutdown":
        stop()
        return
      default: break
      }
    }
  }
}

final class FakeRunning: RunningEngine, @unchecked Sendable {
  let engine: FakeEngine
  private var killed = false
  init(_ engine: FakeEngine) { self.engine = engine }
  func terminate() { engine.stop() }
  func kill() { engine.stop() }
  func waitUntilExit() async {
    await withCheckedContinuation { continuation in
      DispatchQueue.global().async {
        self.engine.waitForExit()
        continuation.resume()
      }
    }
  }
}

final class FakeLauncher: EngineLaunching, @unchecked Sendable {
  private let lock = NSLock()
  private var modes: [FakeEngine.Mode]
  private(set) var engines: [FakeEngine] = []
  init(_ modes: [FakeEngine.Mode]) { self.modes = modes }
  var launches: Int { lock.withLock { engines.count } }
  func launch(_ request: EngineLaunchRequest) throws -> RunningEngine {
    let mode = lock.withLock { modes.isEmpty ? FakeEngine.Mode.healthy : modes.removeFirst() }
    let engine = try FakeEngine(directory: request.controlDirectory, mode: mode)
    lock.withLock { engines.append(engine) }
    return FakeRunning(engine)
  }
}

struct FakeInstaller: EngineInstalling {
  func install() throws -> EngineLayout { EngineLayout(root: URL(fileURLWithPath: "/nonexistent")) }
}

func shortDirectory() -> URL {
  URL(fileURLWithPath: "/tmp/firra-t-\(UUID().uuidString.prefix(8))")
}

func until(_ seconds: Double = 5, _ condition: @Sendable () async -> Bool) async -> Bool {
  let deadline = Date().addingTimeInterval(seconds)
  while Date() < deadline {
    if await condition() { return true }
    try? await Task.sleep(for: .milliseconds(20))
  }
  return await condition()
}

func fastTiming() -> EngineTiming {
  var t = EngineTiming()
  t.connectTimeout = 2
  t.handshakeTimeout = 3
  t.pingInterval = 0.05
  t.pongTimeout = 0.05
  t.maxMissedPongs = 2
  t.shutdownGrace = 2
  t.killAfter = 1
  t.backoff = { _ in 0.05 }
  return t
}

actor Collector<T: Sendable> {
  private(set) var items: [T] = []
  func add(_ item: T) { items.append(item) }
}

// MARK: - tests

@Suite struct EngineProtocolTests {
  @Test func secretSlotsFollowTheContractPattern() {
    #expect(SecretSlot.isValid("db_encryption_key"))
    #expect(SecretSlot.isValid("device_signing_key"))
    #expect(SecretSlot.isValid("google_refresh_token:5b0c3f7e-1f0a-4c55-9d5e-2f8d6f0f7a11"))
    #expect(SecretSlot.isValid("slack_user_token:abc_DEF-1"))
    #expect(!SecretSlot.isValid("google_refresh_token:"))
    #expect(!SecretSlot.isValid("google_refresh_token:../../etc"))
    #expect(!SecretSlot.isValid("slack_user_token:" + String(repeating: "a", count: 65)))
    #expect(!SecretSlot.isValid("anything_else"))
  }

  @Test func messagesEncodeToTheContractFieldNames() throws {
    let json = try JSONEncoder().encode(HandshakeMessage.hello(appVersion: "1.0"))
    let object = try #require(try JSONSerialization.jsonObject(with: json) as? [String: Any])
    #expect(object["type"] as? String == "hello")
    #expect(object["protocol"] as? Int == 1)
    #expect(object["app_version"] as? String == "1.0")
    let ready = try JSONDecoder().decode(
      HandshakeMessage.self, from: Data(#"{"type":"ready","api_port":4242,"api_token":"x"}"#.utf8))
    #expect(ready.apiPort == 4242 && ready.apiToken == "x")
  }

  @Test func ensureDatabaseKeyCreatesAThirtyTwoByteKeyOnce() throws {
    let secrets = InMemoryEngineSecrets()
    #expect(try secrets.ensureDatabaseKey())
    let key = try #require(try secrets.all()[SecretSlot.databaseKey])
    #expect(Data(base64Encoded: key)?.count == 32)
    #expect(try !secrets.ensureDatabaseKey())
    #expect(try secrets.all()[SecretSlot.databaseKey] == key)
  }
}

@Suite(.serialized) struct EngineSupervisorTests {
  private func make(
    modes: [FakeEngine.Mode] = [.healthy], secrets: InMemoryEngineSecrets = InMemoryEngineSecrets(),
    collector: Collector<EngineEvent>? = nil
  ) -> (EngineSupervisor, FakeLauncher, URL, InMemoryEngineSecrets) {
    let dir = shortDirectory()
    let launcher = FakeLauncher(modes)
    let supervisor = EngineSupervisor(
      home: dir, controlDirectory: dir.appending(path: "run"), appVersion: "1.2.3",
      installer: FakeInstaller(), launcher: launcher, secrets: secrets, timing: fastTiming(),
      onEvent: { event in if let collector { Task { await collector.add(event) } } })
    return (supervisor, launcher, dir, secrets)
  }

  @Test func handshakeSendsSecretsInOrderThenReachesRunning() async throws {
    let secrets = InMemoryEngineSecrets([
      SecretSlot.databaseKey: "a2V5", SecretSlot.googleRefreshToken("c1"): "1//refresh",
    ])
    let (supervisor, launcher, dir, _) = make(secrets: secrets)
    defer { try? FileManager.default.removeItem(at: dir) }
    await supervisor.start()
    #expect(await until { await supervisor.endpoint != nil })
    let endpoint = try #require(await supervisor.endpoint)
    #expect(endpoint.port == 4242 && endpoint.token.count == 40)
    #expect(endpoint.baseURL.absoluteString == "http://127.0.0.1:4242")
    let types = launcher.engines[0].received.map(\.type)
    #expect(types == ["hello", "secret_set", "secret_set", "secrets_complete"])
    #expect(launcher.engines[0].received[0].appVersion == "1.2.3")
    #expect(launcher.engines[0].received.filter { $0.type == "secret_set" }.compactMap(\.name)
      == [SecretSlot.databaseKey, SecretSlot.googleRefreshToken("c1")])
    await supervisor.stop()
    #expect(launcher.engines[0].received.last?.type == "shutdown")
    #expect(await supervisor.endpoint == nil)
  }

  @Test func pingsAreAnsweredAndTheEngineStaysUp() async throws {
    let (supervisor, launcher, dir, _) = make()
    defer { try? FileManager.default.removeItem(at: dir) }
    await supervisor.start()
    #expect(await until { await supervisor.endpoint != nil })
    #expect(await until { launcher.engines[0].received.filter { $0.type == "ping" }.count >= 3 })
    #expect(launcher.launches == 1)
    await supervisor.stop()
  }

  @Test func anEngineThatStopsAnsweringPingsIsRestarted() async throws {
    let (supervisor, launcher, dir, _) = make(modes: [.silent, .healthy])
    defer { try? FileManager.default.removeItem(at: dir) }
    await supervisor.start()
    #expect(await until(10) { launcher.launches >= 2 })
    #expect(await until(10) { await supervisor.endpoint != nil })
    await supervisor.stop()
  }

  @Test func aCrashedEngineProcessIsRestarted() async throws {
    let (supervisor, launcher, dir, _) = make()
    defer { try? FileManager.default.removeItem(at: dir) }
    await supervisor.start()
    #expect(await until { await supervisor.endpoint != nil })
    launcher.engines[0].stop()  // the process dies without a shutdown message
    #expect(await until(10) { launcher.launches >= 2 })
    #expect(await until(10) { await supervisor.endpoint != nil })
    await supervisor.stop()
  }

  @Test func aRefusalMakesTheStateFailedAndIsRetried() async throws {
    let (supervisor, launcher, dir, _) = make(modes: [.refuse("protocol_mismatch"), .healthy])
    defer { try? FileManager.default.removeItem(at: dir) }
    let states = Collector<EngineState>()
    let stream = await supervisor.states()
    let watcher = Task { for await s in stream { await states.add(s) } }
    await supervisor.start()
    #expect(await until(10) { await supervisor.endpoint != nil })
    #expect(launcher.launches >= 2)
    let seen = await states.items
    #expect(seen.contains(.failed("engine refused: protocol_mismatch")))
    watcher.cancel()
    await supervisor.stop()
  }

  @Test func engineEventsAndRotatedSecretsReachTheApp() async throws {
    let events = Collector<EngineEvent>()
    let secrets = InMemoryEngineSecrets()
    let (supervisor, launcher, dir, _) = make(secrets: secrets, collector: events)
    defer { try? FileManager.default.removeItem(at: dir) }
    await supervisor.start()
    #expect(await until { await supervisor.endpoint != nil })
    var reauth = HandshakeMessage(type: "event"); reauth.event = "need_reauth"; reauth.connectionId = "c1"
    launcher.engines[0].push(reauth)
    var rotated = HandshakeMessage.secretSet(name: SecretSlot.googleRefreshToken("c1"), value: "rotated")
    rotated.type = "secret_set"
    launcher.engines[0].push(rotated)
    launcher.engines[0].push(.secretSet(name: "not_a_slot", value: "x"))
    #expect(await until { await events.items.contains(.needReauth(connectionID: "c1")) })
    #expect(await until { (try? secrets.all()[SecretSlot.googleRefreshToken("c1")]) == "rotated" })
    #expect(try secrets.all()["not_a_slot"] == nil)
    await supervisor.stop()
  }

  @Test func engineRequestedSlackDeletionRemovesOnlyTheNamedSecret() async throws {
    let (supervisor, launcher, dir, secrets) = make()
    defer { try? FileManager.default.removeItem(at: dir) }
    try secrets.set("xoxp-synthetic", for: SecretSlot.slackUserToken("s1"))
    try secrets.set("keep", for: SecretSlot.googleRefreshToken("g1"))
    await supervisor.start()
    #expect(await until { await supervisor.endpoint != nil })
    launcher.engines[0].push(.secretDelete(name: SecretSlot.slackUserToken("s1")))
    #expect(await until { (try? secrets.all()[SecretSlot.slackUserToken("s1")]) == nil })
    #expect(try secrets.all()[SecretSlot.googleRefreshToken("g1")] == "keep")
    await supervisor.stop()
  }

  @Test func changingASecretWhileRunningStoresItAndTellsTheEngine() async throws {
    let (supervisor, launcher, dir, secrets) = make()
    defer { try? FileManager.default.removeItem(at: dir) }
    await supervisor.start()
    #expect(await until { await supervisor.endpoint != nil })
    try await supervisor.putSecret("xoxp-1", for: SecretSlot.slackUserToken("s1"))
    #expect(try secrets.all()[SecretSlot.slackUserToken("s1")] == "xoxp-1")
    #expect(await until {
      launcher.engines[0].received.contains { $0.type == "secret_set" && $0.name == SecretSlot.slackUserToken("s1") }
    })
    try await supervisor.deleteSecret(SecretSlot.slackUserToken("s1"))
    #expect(try secrets.all()[SecretSlot.slackUserToken("s1")] == nil)
    await #expect(throws: EngineError.self) {
      try await supervisor.putSecret("v", for: "bad slot")
    }
    await supervisor.stop()
  }
}

@Suite struct EngineInstallerTests {
  private func fixture() throws -> (bundle: URL, home: URL) {
    let root = shortDirectory()
    let bundle = root.appending(path: "bundle/engine")
    try FileManager.default.createDirectory(at: bundle.appending(path: "python/bin"), withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: bundle.appending(path: "postgres/bin"), withIntermediateDirectories: true)
    let python = bundle.appending(path: "python/bin/python3.12")
    try Data("#!/bin/sh\n".utf8).write(to: python)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: python.path)
    return (bundle, root.appending(path: "home"))
  }

  @Test func copiesTheEngineClearsQuarantineAndKeepsExecutableBits() throws {
    let (bundle, home) = try fixture()
    defer { try? FileManager.default.removeItem(at: home.deletingLastPathComponent()) }
    let python = bundle.appending(path: "python/bin/python3.12")
    #expect(setxattr(python.path, "com.apple.quarantine", "0081;00000000;Safari;", 21, 0, 0) == 0)
    let layout = try EngineInstaller(bundledEngine: bundle, home: home, buildID: "b1").install()
    #expect(FileManager.default.isExecutableFile(atPath: layout.python.path))
    #expect(getxattr(layout.python.path, "com.apple.quarantine", nil, 0, 0, 0) < 0)
    #expect(layout.postgresBin.path.hasSuffix("postgres/bin"))
  }

  @Test func aSecondInstallDoesNotCopyAgainAndOldBuildsAreRemoved() throws {
    let (bundle, home) = try fixture()
    defer { try? FileManager.default.removeItem(at: home.deletingLastPathComponent()) }
    _ = try EngineInstaller(bundledEngine: bundle, home: home, buildID: "b1").install()
    let marker = home.appending(path: "engine/b1/extra")
    try Data("keep".utf8).write(to: marker)
    _ = try EngineInstaller(bundledEngine: bundle, home: home, buildID: "b1").install()
    #expect(FileManager.default.fileExists(atPath: marker.path))  // not re-copied
    _ = try EngineInstaller(bundledEngine: bundle, home: home, buildID: "b2").install()
    #expect(!FileManager.default.fileExists(atPath: home.appending(path: "engine/b1").path))
    #expect(FileManager.default.fileExists(atPath: home.appending(path: "engine/b2/.complete").path))
  }

  @Test func aMissingBundledEngineIsAnInstallError() throws {
    let root = shortDirectory()
    defer { try? FileManager.default.removeItem(at: root) }
    #expect(throws: EngineError.installFailed("bundled engine missing")) {
      try EngineInstaller(bundledEngine: root, home: root.appending(path: "h"), buildID: "b").install()
    }
  }
}
