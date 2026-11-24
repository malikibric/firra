import CryptoKit
import Foundation

/// RFC 7636 proof key for code exchange (S256), for Google Desktop OAuth.
public struct PKCE: Sendable, Equatable {
  public let verifier: String
  public let challenge: String

  public init(verifier: String) {
    self.verifier = verifier
    self.challenge = Self.base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
  }

  public static func generate() -> PKCE {
    var bytes = [UInt8](repeating: 0, count: 48)
    let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
    precondition(status == errSecSuccess, "no secure random bytes")
    return PKCE(verifier: base64URL(Data(bytes)))
  }

  static func base64URL(_ data: Data) -> String {
    data.base64EncodedString()
      .replacingOccurrences(of: "+", with: "-")
      .replacingOccurrences(of: "/", with: "_")
      .replacingOccurrences(of: "=", with: "")
  }
}
