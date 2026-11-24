import Foundation

public enum GoogleOAuthConfigError: Error, Equatable {
  case missing
  case invalid

  public var userMessage: String {
    switch self {
    case .missing: "Add Google's Desktop client JSON at ~/Library/Application Support/Firra/google-oauth-client.json, then reconnect."
    case .invalid: "The Google OAuth file must be a downloaded Desktop client JSON with installed.client_id and installed.client_secret."
    }
  }
}

/// L5-03 replaces this source with bundled/entered configuration; callers keep this one seam.
public func loadGoogleOAuthConfig(home: URL) throws -> GoogleOAuthConfig {
  let file = home.appending(path: "google-oauth-client.json")
  guard FileManager.default.fileExists(atPath: file.path) else { throw GoogleOAuthConfigError.missing }
  struct Download: Decodable {
    struct Installed: Decodable { let clientId: String; let clientSecret: String }
    let installed: Installed
  }
  do {
    let client = try JSONDecoder.api.decode(Download.self, from: Data(contentsOf: file)).installed
    guard !client.clientId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
      !client.clientSecret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      throw GoogleOAuthConfigError.invalid
    }
    return GoogleOAuthConfig(clientID: client.clientId, clientSecret: client.clientSecret)
  } catch { throw GoogleOAuthConfigError.invalid }
}
