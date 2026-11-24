import Foundation
import Testing

@testable import FirraKit

/// Uses the real Keychain with a throwaway service name: the in-memory fake hid a macOS-only failure (-50).
@Suite struct KeychainSecretsTests {
  @Test func readsSeveralSlotsBackAndCreatesTheKeyOnce() throws {
    let store = KeychainEngineSecrets(service: "app.firra.mac.engine.test-\(UUID().uuidString)")
    defer {
      for name in ["db_encryption_key", "device_signing_key", "google_refresh_token:abc"] { try? store.delete(name) }
    }
    #expect(try store.all().isEmpty)
    #expect(try store.ensureDatabaseKey())
    try store.set("seed-value", for: "device_signing_key")
    try store.set("refresh-value", for: "google_refresh_token:abc")
    let all = try store.all()
    #expect(all.count == 3)
    #expect(all["device_signing_key"] == "seed-value" && all["google_refresh_token:abc"] == "refresh-value")
    #expect(try store.ensureDatabaseKey() == false)
    #expect(all["db_encryption_key"] == (try store.all())["db_encryption_key"])
  }
}
