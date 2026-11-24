import Foundation
import Security
import Testing
@testable import FirraKit

@Suite struct SettingsDataFilesTests {
  @Test func savedFilesAreOwnerOnlyEvenWhenReplacingAPublicFile() throws {
    let root = FileManager.default.temporaryDirectory.appending(path: "firra-file-test-\(UUID())")
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let url = root.appending(path: "backup.firrabackup")
    try Data("old".utf8).write(to: url)
    try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: url.path)
    try SettingsDataFiles.write(Data("encrypted".utf8), to: url)
    #expect(try Data(contentsOf: url) == Data("encrypted".utf8))
    let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
    #expect((attrs[.posixPermissions] as? NSNumber)?.intValue == 0o600)
    #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["backup.firrabackup"])
    #expect(try SettingsDataFiles.readBackup(url) == Data("encrypted".utf8))
  }

  @Test func deletionKeepsOnlyOAuthConfigAndDoesNotFollowLinks() throws {
    let fm = FileManager.default
    let root = fm.temporaryDirectory.appending(path: "firra-delete-test-\(UUID())")
    let home = root.appending(path: "Firra")
    try fm.createDirectory(at: home.appending(path: "engine"), withIntermediateDirectories: true)
    defer { try? fm.removeItem(at: root) }
    let config = home.appending(path: "google-oauth-client.json")
    let contents = Data(#"{"installed":{"client_id":"synthetic"}}"#.utf8)
    try contents.write(to: config)
    try Data("private synthetic data".utf8).write(to: home.appending(path: ".hidden-data"))
    let outside = root.appending(path: "outside")
    try Data("unrelated synthetic file".utf8).write(to: outside)
    try fm.createSymbolicLink(at: home.appending(path: "linked-data"), withDestinationURL: outside)
    try SettingsDataFiles.deleteHome(home)
    #expect(try fm.contentsOfDirectory(atPath: home.path) == ["google-oauth-client.json"])
    #expect(try Data(contentsOf: config) == contents && fm.fileExists(atPath: outside.path))
    try SettingsDataFiles.deleteHome(home)
    try fm.removeItem(at: config); try SettingsDataFiles.deleteHome(home)
    #expect(!fm.fileExists(atPath: home.path))
    let link = root.appending(path: "linked-home")
    try fm.createSymbolicLink(at: link, withDestinationURL: root)
    #expect(throws: CocoaError.self) { try SettingsDataFiles.deleteHome(link) }
    #expect(fm.fileExists(atPath: outside.path))
  }

  @Test func keychainDeletionCoversAllAccountsInBothFirraServicesAndCanRetry() throws {
    var services: [String] = []
    try FirraKeychain.deleteAll { query in
      let values = query as! [String: Any]
      services.append(values[kSecAttrService as String] as! String)
      #expect(values[kSecAttrAccount as String] == nil && values[kSecReturnData as String] == nil)
      return errSecItemNotFound
    }
    #expect(services == ["app.firra.mac.engine", "app.firra.mac.vault"])
    #expect(throws: VaultError.keychain(errSecAuthFailed)) {
      try FirraKeychain.deleteAll { _ in errSecAuthFailed }
    }
    try FirraKeychain.deleteAll { _ in errSecSuccess }
  }
}
