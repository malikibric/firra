import Foundation

/// Local, per-user copy of the last answers, so the app opens instantly and still shows something
/// offline. Holds nothing but what the API already returned to this user; removed on sign-out.
public protocol ResponseCache: Sendable {
  func load(_ key: String) -> Data?
  func save(_ data: Data, for key: String)
  func clearAll()
}

public struct NoCache: ResponseCache {
  public init() {}
  public func load(_ key: String) -> Data? { nil }
  public func save(_ data: Data, for key: String) {}
  public func clearAll() {}
}

/// Files under `~/Library/Application Support/Firra/cache/<user id>/`, readable by this user only.
public final class FileResponseCache: ResponseCache, @unchecked Sendable {
  private let base: URL
  private let lock = NSLock()
  private var userID: String?
  private var retired = false

  public init(base: URL? = nil) {
    self.base =
      base
      ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
      .appending(path: "Firra/cache", directoryHint: .isDirectory)
  }

  /// Selects whose cache is read and written (nil = nobody signed in, nothing is stored).
  public func use(user: String?) { lock.withLock { if !retired { userID = user } } }

  /// Ends this cache for good: its files are removed and nothing is ever written again, so a late
  /// answer of a signed-out session cannot reappear on disk or under another user.
  public func retire() {
    lock.withLock {
      retired = true
      userID = nil
    }
    clearAll()
  }

  private func file(_ key: String) -> URL? {
    guard let user = lock.withLock({ userID }), Self.safe(user), Self.safe(key) else { return nil }
    return base.appending(path: user, directoryHint: .isDirectory).appending(path: key + ".json")
  }

  /// File names are never built from anything but letters, digits, `-` and `_`.
  static func safe(_ part: String) -> Bool {
    !part.isEmpty && part.count <= 80
      && part.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }
  }

  public func load(_ key: String) -> Data? {
    guard let url = file(key) else { return nil }
    return try? Data(contentsOf: url)
  }

  public func save(_ data: Data, for key: String) {
    guard let url = file(key) else { return }
    let directory = url.deletingLastPathComponent()
    try? FileManager.default.createDirectory(
      at: directory, withIntermediateDirectories: true,
      attributes: [.posixPermissions: 0o700])
    try? data.write(to: url, options: .atomic)
    try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
  }

  public func clearAll() {
    try? FileManager.default.removeItem(at: base)
  }
}
