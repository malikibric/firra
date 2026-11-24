import CryptoKit
import Foundation
import Security

/// The key that seals the local mail vault of one user. It never leaves the Keychain of this Mac.
public protocol VaultKeyStore: Sendable {
  func key(for user: String) throws -> SymmetricKey
}

public enum VaultError: Error, Equatable {
  case keychain(Int32)
  case database(String)
  case sealed
}

public struct KeychainVaultKeyStore: VaultKeyStore {
  private let service: String
  public init(service: String = "app.firra.mac.vault") { self.service = service }

  public func key(for user: String) throws -> SymmetricKey {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: user,
    ]
    var read = query
    read[kSecReturnData as String] = true
    read[kSecMatchLimit as String] = kSecMatchLimitOne
    var item: CFTypeRef?
    if SecItemCopyMatching(read as CFDictionary, &item) == errSecSuccess, let data = item as? Data,
      data.count == 32
    {
      return SymmetricKey(data: data)
    }
    let fresh = SymmetricKey(size: .bits256)
    var add = query
    add[kSecValueData as String] = fresh.withUnsafeBytes { Data($0) }
    add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    let status = SecItemAdd(add as CFDictionary, nil)
    guard status == errSecSuccess else { throw VaultError.keychain(status) }
    return fresh
  }
}

public final class InMemoryVaultKeyStore: VaultKeyStore, @unchecked Sendable {
  private let lock = NSLock()
  private var keys: [String: SymmetricKey] = [:]
  public init() {}
  public func key(for user: String) throws -> SymmetricKey {
    lock.withLock {
      if let existing = keys[user] { return existing }
      let fresh = SymmetricKey(size: .bits256)
      keys[user] = fresh
      return fresh
    }
  }
}
