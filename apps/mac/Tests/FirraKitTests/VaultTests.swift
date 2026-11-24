import Foundation
import SQLite3
import Testing
@testable import FirraKit

private func record(_ id: String, body: String = "Please send the plan.", client: String? = "c1",
                    at: String = "2026-10-01T09:00:00Z") -> VaultRecord {
  VaultRecord(
    id: id, workspaceId: "w", provider: "gmail", externalId: "ext-\(id)", kind: "email",
    clientId: client, threadKey: "t", occurredAt: at, direction: "in", subject: "Plan",
    deepLink: nil, contentHash: "h-\(id)", wordCount: 4, body: body)
}

private func folder() -> URL {
  FileManager.default.temporaryDirectory.appending(path: "firra-vault-\(UUID().uuidString)")
}

private func recordJSON(_ id: String, body: String) -> String {
  """
  {"id":"\(id)","workspace_id":"w","provider":"gmail","external_id":"e-\(id)","kind":"email",
   "client_id":"c1","thread_key":null,"occurred_at":"2026-10-01T09:00:00Z","direction":"in",
   "subject":"Plan","deep_link":null,"content_hash":null,"word_count":4,"body":"\(body)"}
  """
}

@Suite struct VaultStoreTests {
  @Test func storesAndReadsBackNewestFirstPerClient() async throws {
    let dir = folder()
    defer { try? FileManager.default.removeItem(at: dir) }
    let store = try VaultStore(directory: dir, userID: "u1", keys: InMemoryVaultKeyStore())
    try await store.upsert([
      record("a", at: "2026-10-01T09:00:00Z"), record("b", client: "c2", at: "2026-10-02T09:00:00Z"),
    ])
    #expect(try await store.recent().map(\.id) == ["b", "a"])
    #expect(try await store.recent(clientId: "c1").map(\.id) == ["a"])
    #expect(try await store.record(id: "a")?.body == "Please send the plan.")
    #expect(try await store.count() == 2)
    try await store.upsert([record("a", body: "Changed text.")])
    #expect(try await store.count() == 2)
    #expect(try await store.record(id: "a")?.body == "Changed text.")
  }

  @Test func theFileOnDiskHoldsNoReadableMailAndIsOwnerOnly() async throws {
    let dir = folder()
    defer { try? FileManager.default.removeItem(at: dir) }
    let store = try VaultStore(directory: dir, userID: "u1", keys: InMemoryVaultKeyStore())
    try await store.upsert([record("a", body: "Highly confidential renewal figure 123456.")])
    let file = dir.appending(path: "u1/vault.sqlite")
    let raw = try Data(contentsOf: file)
    #expect(raw.range(of: Data("confidential".utf8)) == nil)
    #expect(raw.range(of: Data("123456".utf8)) == nil)
    let mode = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? Int
    #expect(mode == 0o600)
  }

  @Test func aDifferentKeyCannotOpenTheMail() async throws {
    let dir = folder()
    defer { try? FileManager.default.removeItem(at: dir) }
    let first = try VaultStore(directory: dir, userID: "u1", keys: InMemoryVaultKeyStore())
    try await first.upsert([record("a")])
    let other = try VaultStore(directory: dir, userID: "u1", keys: InMemoryVaultKeyStore())
    await #expect(throws: VaultError.sealed) { _ = try await other.record(id: "a") }
  }

  @Test func usersAreSeparatedAndHostileIdsRefused() async throws {
    let dir = folder()
    defer { try? FileManager.default.removeItem(at: dir) }
    let keys = InMemoryVaultKeyStore()
    let one = try VaultStore(directory: dir, userID: "u1", keys: keys)
    try await one.upsert([record("a")])
    let two = try VaultStore(directory: dir, userID: "u2", keys: keys)
    #expect(try await two.count() == 0)
    #expect(throws: VaultError.self) { _ = try VaultStore(directory: dir, userID: "../u1", keys: keys) }
    #expect(throws: VaultError.self) { _ = try VaultStore(directory: dir, userID: "", keys: keys) }
  }

  @Test func deleteAllEmptiesTheVaultAndItStaysUsable() async throws {
    let dir = folder()
    defer { try? FileManager.default.removeItem(at: dir) }
    let store = try VaultStore(directory: dir, userID: "u1", keys: InMemoryVaultKeyStore())
    try await store.upsert([record("a")])
    try await store.deleteAll()
    #expect(try await store.count() == 0)
    try await store.upsert([record("b")])  // fetching works again after removal
    #expect(try await store.record(id: "b") != nil)
  }

  @Test func aFailedRemovalIsReportedAndTheOldMailStaysReadable() async throws {
    let dir = folder()
    let user = dir.appending(path: "u1")
    defer {
      try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: user.path)
      try? FileManager.default.removeItem(at: dir)
    }
    let store = try VaultStore(directory: dir, userID: "u1", keys: InMemoryVaultKeyStore())
    try await store.upsert([record("a")])
    try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: user.path)
    await #expect(throws: VaultError.self) { try await store.deleteAll() }
    #expect(try await store.record(id: "a")?.body == "Please send the plan.")
  }

  @Test func oneDamagedMessageDoesNotHideTheOthers() async throws {
    let dir = folder()
    defer { try? FileManager.default.removeItem(at: dir) }
    let store = try VaultStore(directory: dir, userID: "u1", keys: InMemoryVaultKeyStore())
    try await store.upsert([record("a", at: "2026-10-01T09:00:00Z"), record("b", at: "2026-10-02T09:00:00Z")])
    // A third row whose sealed bytes are garbage (damaged file, or sealed under another key).
    var db: OpaquePointer?
    #expect(sqlite3_open(dir.appending(path: "u1/vault.sqlite").path, &db) == SQLITE_OK)
    #expect(
      sqlite3_exec(
        db,
        "insert into records (id, occurred_at, kind, client_id, content_hash, sealed) values ('bad','2026-10-03T09:00:00Z','email','c1','h', x'0102030405')",
        nil, nil, nil) == SQLITE_OK)
    sqlite3_close(db)
    let shown = try await store.recentWithDamage()
    #expect(shown.records.map(\.id) == ["b", "a"] && shown.unreadable == 1)
  }

  @Test func deletesEveryMessageOfOneClientOnly() async throws {
    let dir = folder()
    defer { try? FileManager.default.removeItem(at: dir) }
    let store = try VaultStore(directory: dir, userID: "u1", keys: InMemoryVaultKeyStore())
    try await store.upsert([record("a", client: "c1"), record("b", client: "c2"), record("c", client: "c1")])
    try await store.deleteClient("c1")
    #expect(try await store.recent().map(\.id) == ["b"])
  }
}

