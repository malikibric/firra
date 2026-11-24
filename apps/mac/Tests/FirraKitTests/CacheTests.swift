import Foundation
import Testing
@testable import FirraKit

@Suite struct FileResponseCacheTests {
  func make() -> (FileResponseCache, URL) {
    let dir = FileManager.default.temporaryDirectory.appending(path: "firra-cache-\(UUID().uuidString)")
    return (FileResponseCache(base: dir), dir)
  }

  @Test func storesPerUserAndNothingWithoutOne() {
    let (cache, dir) = make()
    defer { try? FileManager.default.removeItem(at: dir) }
    cache.save(Data("x".utf8), for: "me")
    #expect(cache.load("me") == nil)
    cache.use(user: "user-a")
    cache.save(Data("a".utf8), for: "me")
    #expect(cache.load("me") == Data("a".utf8))
    cache.use(user: "user-b")
    #expect(cache.load("me") == nil)
    cache.use(user: "user-a")
    #expect(cache.load("me") == Data("a".utf8))
  }

  @Test func clearAllRemovesEverythingAndFilesAreOwnerOnly() throws {
    let (cache, dir) = make()
    defer { try? FileManager.default.removeItem(at: dir) }
    cache.use(user: "user-a")
    cache.save(Data("a".utf8), for: "me")
    let file = dir.appending(path: "user-a/me.json")
    let mode = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? Int
    #expect(mode == 0o600)
    cache.clearAll()
    #expect(!FileManager.default.fileExists(atPath: dir.path))
    #expect(cache.load("me") == nil)
  }

  @Test func neverBuildsPathsFromHostileNames() {
    let (cache, dir) = make()
    defer { try? FileManager.default.removeItem(at: dir) }
    cache.use(user: "../../etc")
    cache.save(Data("x".utf8), for: "me")
    cache.use(user: "user-a")
    cache.save(Data("x".utf8), for: "../me")
    #expect(cache.load("../me") == nil)
    #expect(!FileManager.default.fileExists(atPath: dir.path))
  }
}

actor Flag {
  private(set) var value = false
  func set() { value = true }
}

@MainActor @Suite struct OfflineTests {
  let item = #"{"client_id":"c","name":"Redfern","status":"active","policy":"full","as_of":"2026-10-01","risk_state":"high","previous_risk_state":null,"state_since":"2026-09-25","expansion_state":"none","strength":"weak","play":null,"days_to_notice":null,"drivers":[]}"#

  func api(_ replies: [StubTransport.Reply]) -> APIClient {
    APIClient(base: URL(string: "https://api.example")!, transport: StubTransport(replies), tokenProvider: { "tok" })
  }

  @Test func offlineKeepsTheLocalCopyAndSaysSo() async {
    let dir = FileManager.default.temporaryDirectory.appending(path: "firra-off-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: dir) }
    let cache = FileResponseCache(base: dir)
    cache.use(user: "u")
    let online = AttentionModel(api: api([.init(status: 200, body: #"{"generated_at":"x","items":[\#(item)]}"#)]), cache: cache)
    await online.load(workspaceId: "w")
    #expect(online.items.count == 1 && !online.isOffline)

    // A new launch without a network: the saved list shows, marked as not refreshed.
    let offline = AttentionModel(api: api([.init(status: 500, body: "{}")]), cache: cache)
    await offline.load(workspaceId: "w")
    #expect(offline.items.map(\.name) == ["Redfern"] && offline.isOffline && offline.phase == .ready)
  }

  @Test func withoutACopyAnErrorStaysAnError() async {
    let model = AttentionModel(api: api([.init(status: 500, body: "{}")]), cache: NoCache())
    await model.load(workspaceId: "w")
    if case .failed = model.phase {} else { Issue.record("expected a failure") }
  }


  @Test func aRejectedEngineTokenCallsTheRestartHook() async {
    let called = Flag()
    let client = APIClient(
      base: URL(string: "https://api.example")!,
      transport: StubTransport([.init(status: 401, body: #"{"error":"unauthorized","message":"m","request_id":"r"}"#)]),
      tokenProvider: { "tok" }, onUnauthorized: { await called.set() })
    await #expect(throws: APIError.unauthenticated) { _ = try await client.me() }
    #expect(await called.value)
  }

}
