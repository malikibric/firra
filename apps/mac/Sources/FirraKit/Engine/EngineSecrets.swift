import Foundation
import Security

/// Where the engine's long-lived secrets live on this Mac (Google and Slack tokens, the database encryption
/// key, the device key). The app hands them to the engine over the control socket at every start; the engine
/// keeps them in memory only.
public protocol EngineSecrets: Sendable {
  func all() throws -> [String: String]
  func set(_ value: String, for name: String) throws
  func delete(_ name: String) throws
}

extension EngineSecrets {
  /// The database encryption key is created by the app on first run (32 random bytes, base64) and never leaves
  /// the Keychain except through the control socket. Returns true when it was just created.
  @discardableResult
  public func ensureDatabaseKey() throws -> Bool {
    if try all()[SecretSlot.databaseKey] != nil { return false }
    var bytes = [UInt8](repeating: 0, count: 32)
    guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
      throw EngineError.launchFailed("no random bytes")
    }
    try set(Data(bytes).base64EncodedString(), for: SecretSlot.databaseKey)
    return true
  }
}

public final class InMemoryEngineSecrets: EngineSecrets, @unchecked Sendable {
  private let lock = NSLock()
  private var values: [String: String]
  public init(_ values: [String: String] = [:]) { self.values = values }
  public func all() throws -> [String: String] { lock.withLock { values } }
  public func set(_ value: String, for name: String) throws { lock.withLock { values[name] = value } }
  public func delete(_ name: String) throws { _ = lock.withLock { values.removeValue(forKey: name) } }
}

/// One generic-password item per slot, readable only after the first unlock and never synced.
public struct KeychainEngineSecrets: EngineSecrets {
  private let service: String
  public init(service: String = "app.firra.mac.engine") { self.service = service }

  private func query(_ name: String? = nil) -> [String: Any] {
    var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service]
    if let name { q[kSecAttrAccount as String] = name }
    return q
  }

  /// macOS' file-based Keychain refuses `kSecReturnData` together with `kSecMatchLimitAll` (errSecParam, -50), so the
  /// slots are listed first and each value is read on its own.
  public func all() throws -> [String: String] {
    var list = query()
    list[kSecReturnAttributes as String] = true
    list[kSecMatchLimit as String] = kSecMatchLimitAll
    var result: CFTypeRef?
    let status = SecItemCopyMatching(list as CFDictionary, &result)
    if status == errSecItemNotFound { return [:] }
    guard status == errSecSuccess, let items = result as? [[String: Any]] else {
      throw EngineError.launchFailed("keychain read \(status)")
    }
    var out: [String: String] = [:]
    for item in items {
      guard let name = item[kSecAttrAccount as String] as? String, SecretSlot.isValid(name) else { continue }
      var one = query(name)
      one[kSecReturnData as String] = true
      one[kSecMatchLimit as String] = kSecMatchLimitOne
      var data: CFTypeRef?
      let read = SecItemCopyMatching(one as CFDictionary, &data)
      if read == errSecItemNotFound { continue }
      guard read == errSecSuccess, let bytes = data as? Data, let text = String(data: bytes, encoding: .utf8) else {
        throw EngineError.launchFailed("keychain read \(read)")
      }
      out[name] = text
    }
    return out
  }

  public func set(_ value: String, for name: String) throws {
    guard SecretSlot.isValid(name) else { throw EngineError.protocolViolation("unknown secret slot") }
    let data = Data(value.utf8)
    let status = SecItemUpdate(query(name) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecSuccess { return }
    guard status == errSecItemNotFound else { throw EngineError.launchFailed("keychain update \(status)") }
    var add = query(name)
    add[kSecValueData as String] = data
    add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    let added = SecItemAdd(add as CFDictionary, nil)
    guard added == errSecSuccess else { throw EngineError.launchFailed("keychain add \(added)") }
  }

  public func delete(_ name: String) throws {
    let status = SecItemDelete(query(name) as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw EngineError.launchFailed("keychain delete \(status)")
    }
  }
}
