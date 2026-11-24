import CryptoKit
import Foundation
import SQLite3

/// One delivered mail message. Everything but the index columns is sealed on disk.
public struct VaultRecord: Codable, Sendable, Equatable, Identifiable {
  public let id: String
  public let workspaceId: String
  public let provider: String
  public let externalId: String
  public let kind: String
  public let clientId: String?
  public let threadKey: String?
  public let occurredAt: String
  public let direction: String
  public let subject: String?
  public let deepLink: String?
  public let contentHash: String?
  public let wordCount: Int
  public let body: String
}

/// The user's local mail vault: a SQLite file in Application Support whose messages are sealed with
/// AES-GCM under a key held in the Keychain. Only index columns (id, date, kind, client, hash) are
/// readable without the key. Holds nothing but what the API delivered to this user.
public actor VaultStore {
  /// Owns the SQLite handle so it is closed when the store goes away.
  private final class Handle: @unchecked Sendable {
    var pointer: OpaquePointer?
    deinit { sqlite3_close(pointer) }
  }

  private let handle = Handle()
  private var db: OpaquePointer? {
    get { handle.pointer }
    set { handle.pointer = newValue }
  }
  private let key: SymmetricKey
  private let fileURL: URL

  public init(directory: URL, userID: String, keys: any VaultKeyStore) throws {
    guard Self.safe(userID) else { throw VaultError.database("invalid user") }
    key = try keys.key(for: userID)
    let folder = directory.appending(path: userID, directoryHint: .isDirectory)
    try FileManager.default.createDirectory(
      at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    fileURL = folder.appending(path: "vault.sqlite")
    handle.pointer = try Self.openDatabase(at: fileURL)
    try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
  }

  private static func openDatabase(at url: URL) throws -> OpaquePointer? {
    var opened: OpaquePointer?
    guard
      sqlite3_open_v2(url.path, &opened, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil)
        == SQLITE_OK
    else {
      sqlite3_close(opened)
      throw VaultError.database("open")
    }
    try exec(
      opened,
      """
      create table if not exists records (
        id text primary key, occurred_at text not null, kind text not null, client_id text,
        content_hash text not null, sealed blob not null
      );
      create index if not exists records_client on records (client_id, occurred_at);
      create table if not exists meta (key text primary key, value text not null);
      """)
    try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    return opened
  }

  static func safe(_ part: String) -> Bool {
    !part.isEmpty && part.count <= 80
      && part.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }
  }

  public func upsert(_ records: [VaultRecord]) throws {
    try Self.exec(db, "begin")
    do {
      for record in records {
        let sealed = try seal(record)
        let statement = try prepare(
          "insert or replace into records (id, occurred_at, kind, client_id, content_hash, sealed) values (?, ?, ?, ?, ?, ?)")
        defer { sqlite3_finalize(statement) }
        bind(statement, 1, record.id)
        bind(statement, 2, record.occurredAt)
        bind(statement, 3, record.kind)
        bind(statement, 4, record.clientId)
        bind(statement, 5, record.contentHash ?? "")
        sealed.withUnsafeBytes { raw in
          _ = sqlite3_bind_blob(statement, 6, raw.baseAddress, Int32(sealed.count), unsafeBitCast(-1, to: sqlite3_destructor_type.self))
        }
        guard sqlite3_step(statement) == SQLITE_DONE else { throw VaultError.database("write") }
      }
      try Self.exec(db, "commit")
    } catch {
      try? Self.exec(db, "rollback")
      throw error
    }
  }

  public func record(id: String) throws -> VaultRecord? {
    let statement = try prepare("select sealed from records where id = ?")
    defer { sqlite3_finalize(statement) }
    bind(statement, 1, id)
    guard sqlite3_step(statement) == SQLITE_ROW else { return nil }
    return try open(statement, column: 0)
  }

  /// Newest first. Opens each message, so keep `limit` small. A message that cannot be opened is
  /// skipped and counted in `unreadable`, so one damaged record never hides the healthy ones.
  public func recent(limit: Int = 50, clientId: String? = nil) throws -> [VaultRecord] {
    try recentWithDamage(limit: limit, clientId: clientId).records
  }

  public func recentWithDamage(limit: Int = 50, clientId: String? = nil) throws
    -> (records: [VaultRecord], unreadable: Int)
  {
    let statement = try prepare(
      clientId == nil
        ? "select sealed from records order by occurred_at desc limit ?"
        : "select sealed from records where client_id = ? order by occurred_at desc limit ?")
    defer { sqlite3_finalize(statement) }
    if let clientId {
      bind(statement, 1, clientId)
      sqlite3_bind_int(statement, 2, Int32(limit))
    } else {
      sqlite3_bind_int(statement, 1, Int32(limit))
    }
    var result: [VaultRecord] = []
    var unreadable = 0
    while sqlite3_step(statement) == SQLITE_ROW {
      if let record = try? open(statement, column: 0) { result.append(record) } else { unreadable += 1 }
    }
    return (result, unreadable)
  }

  /// The message an evidence quote came from: ids start with the quote's 8-character source ref, and
  /// the date must match too (8 characters alone could, rarely, match two messages).
  public func record(sourceRef: String, occurredAt: String) throws -> VaultRecord? {
    guard sourceRef.count == 8, sourceRef.allSatisfy({ $0.isHexDigit || $0 == "-" }) else { return nil }
    let statement = try prepare("select sealed from records where id like ? limit 5")
    defer { sqlite3_finalize(statement) }
    bind(statement, 1, sourceRef + "%")
    var day = String(occurredAt.prefix(10))
    if day.isEmpty { day = "-" }
    while sqlite3_step(statement) == SQLITE_ROW {
      if let found = try? open(statement, column: 0), found.occurredAt.hasPrefix(day) { return found }
    }
    return nil
  }

  /// The clients whose mail is held here.
  public func clientIDs() throws -> [String] {
    let statement = try prepare("select distinct client_id from records where client_id is not null")
    defer { sqlite3_finalize(statement) }
    var ids: [String] = []
    while sqlite3_step(statement) == SQLITE_ROW {
      if let text = sqlite3_column_text(statement, 0) { ids.append(String(cString: text)) }
    }
    return ids
  }

  /// Removes every message of one client (its policy became metadata only or excluded).
  public func deleteClient(_ clientId: String) throws {
    let statement = try prepare("delete from records where client_id = ?")
    defer { sqlite3_finalize(statement) }
    bind(statement, 1, clientId)
    guard sqlite3_step(statement) == SQLITE_DONE else { throw VaultError.database("delete") }
  }

  public func count() throws -> Int {
    let statement = try prepare("select count(*) from records")
    defer { sqlite3_finalize(statement) }
    return sqlite3_step(statement) == SQLITE_ROW ? Int(sqlite3_column_int64(statement, 0)) : 0
  }

  public func lastSyncedAt() throws -> String? {
    let statement = try prepare("select value from meta where key = 'last_synced_at'")
    defer { sqlite3_finalize(statement) }
    guard sqlite3_step(statement) == SQLITE_ROW, let text = sqlite3_column_text(statement, 0) else { return nil }
    return String(cString: text)
  }

  public func markSynced(at date: String) throws {
    let statement = try prepare("insert or replace into meta (key, value) values ('last_synced_at', ?)")
    defer { sqlite3_finalize(statement) }
    bind(statement, 1, date)
    guard sqlite3_step(statement) == SQLITE_DONE else { throw VaultError.database("write") }
  }

  /// Removes every message and the file itself ("Remove mail from this Mac"), then opens a fresh empty
  /// vault so fetching works again. A failed removal is reported and the old vault stays usable.
  public func deleteAll() throws {
    sqlite3_close(handle.pointer)
    handle.pointer = nil
    do {
      if FileManager.default.fileExists(atPath: fileURL.path) {
        try FileManager.default.removeItem(at: fileURL)
      }
      for suffix in ["-wal", "-shm", "-journal"] {
        try? FileManager.default.removeItem(atPath: fileURL.path + suffix)
      }
    } catch {
      handle.pointer = try? Self.openDatabase(at: fileURL)
      throw VaultError.database("remove")
    }
    handle.pointer = try Self.openDatabase(at: fileURL)
  }

  // MARK: sealing

  private func seal(_ record: VaultRecord) throws -> Data {
    let plain = try JSONEncoder().encode(record)
    guard let box = try? AES.GCM.seal(plain, using: key), let combined = box.combined else {
      throw VaultError.sealed
    }
    return combined
  }

  private func open(_ statement: OpaquePointer?, column: Int32) throws -> VaultRecord {
    guard let bytes = sqlite3_column_blob(statement, column) else { throw VaultError.sealed }
    let data = Data(bytes: bytes, count: Int(sqlite3_column_bytes(statement, column)))
    guard let box = try? AES.GCM.SealedBox(combined: data),
      let plain = try? AES.GCM.open(box, using: key),
      let record = try? JSONDecoder().decode(VaultRecord.self, from: plain)
    else { throw VaultError.sealed }
    return record
  }

  // MARK: SQLite plumbing

  private func prepare(_ sql: String) throws -> OpaquePointer? {
    var statement: OpaquePointer?
    guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else {
      throw VaultError.database("prepare")
    }
    return statement
  }

  private func bind(_ statement: OpaquePointer?, _ index: Int32, _ value: String?) {
    if let value {
      sqlite3_bind_text(statement, index, value, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
    } else {
      sqlite3_bind_null(statement, index)
    }
  }

  private static func exec(_ handle: OpaquePointer?, _ sql: String) throws {
    guard sqlite3_exec(handle, sql, nil, nil, nil) == SQLITE_OK else {
      throw VaultError.database("exec")
    }
  }
}
