import Darwin
import Foundation
import Security

public enum SettingsDataFiles {
  /// Create the replacement with owner-only permissions before any bytes are written.
  public static func write(_ data: Data, to url: URL) throws {
    let scoped = url.startAccessingSecurityScopedResource()
    defer { if scoped { url.stopAccessingSecurityScopedResource() } }
    let temporary = url.deletingLastPathComponent().appending(path: ".firra-save-\(UUID())")
    defer { try? FileManager.default.removeItem(at: temporary) }
    guard FileManager.default.createFile(atPath: temporary.path, contents: data,
      attributes: [.posixPermissions: 0o600]) else { throw CocoaError(.fileWriteUnknown) }
    guard rename(temporary.path, url.path) == 0 else { throw CocoaError(.fileWriteUnknown) }
  }
  public static func readBackup(_ url: URL) throws -> Data {
    let scoped = url.startAccessingSecurityScopedResource()
    defer { if scoped { url.stopAccessingSecurityScopedResource() } }
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    let limit = 25_165_824 // The contract's 32 MiB base64 limit, decoded.
    let data = try handle.read(upToCount: limit + 1) ?? Data()
    guard !data.isEmpty, data.count <= limit else { throw CocoaError(.fileReadCorruptFile) }
    return data
  }
  /// Preserve only the developer's Desktop OAuth config; never follow a data-directory symlink.
  public static func deleteHome(_ home: URL) throws {
    let fm = FileManager.default
    if (try? home.resourceValues(forKeys: [.isSymbolicLinkKey]))?.isSymbolicLink == true {
      throw CocoaError(.fileWriteNoPermission)
    }
    guard fm.fileExists(atPath: home.path) else { return }
    let children = try fm.contentsOfDirectory(at: home, includingPropertiesForKeys: nil)
    for child in children where child.lastPathComponent != "google-oauth-client.json" {
      try fm.removeItem(at: child)
    }
    if !fm.fileExists(atPath: home.appending(path: "google-oauth-client.json").path) {
      try fm.removeItem(at: home)
    }
  }
}

public enum FirraKeychain {
  /// Delete all accounts, including obsolete slots, from every Keychain service used by this app.
  public static func deleteAll(delete: (CFDictionary) -> OSStatus = { SecItemDelete($0) }) throws {
    for service in ["app.firra.mac.engine", "app.firra.mac.vault"] {
      let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service]
      let status = delete(query as CFDictionary)
      guard status == errSecSuccess || status == errSecItemNotFound else { throw VaultError.keychain(status) }
    }
  }
}
