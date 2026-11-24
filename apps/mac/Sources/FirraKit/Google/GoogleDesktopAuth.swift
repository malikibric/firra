import Foundation

public struct GoogleOAuthConfig: Sendable, Equatable {
  public let clientID: String
  /// A Desktop client's secret is not confidential (Google's own guidance), but it is still never logged.
  public let clientSecret: String
  public init(clientID: String, clientSecret: String) {
    self.clientID = clientID
    self.clientSecret = clientSecret
  }
}

public enum GoogleSignInError: Error, Equatable {
  case cancelled
  case denied(String)
  case stateMismatch
  case noRefreshToken
  case missingScopes([String])
  case exchangeFailed(Int)
  case timeout
  case transport
}

public struct GoogleSignInResult: Sendable, Equatable {
  public let refreshToken: String
  public let email: String
}

public protocol URLOpening: Sendable {
  func open(_ url: URL) async -> Bool
}

/// The Desktop-app OAuth flow of Firra Local v1 (docs/plans/local-v1.md T17): the system browser, a loopback redirect
/// on `127.0.0.1`, PKCE (S256) and a `state` check. The result is a long-lived **refresh token** that goes to the
/// Keychain and then to the engine over the control socket; nothing is stored here.
public struct GoogleDesktopAuth: Sendable {
  public static let scopes = [
    "openid", "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
  ]
  static let authorizeURL = URL(string: "https://accounts.google.com/o/oauth2/v2/auth")!
  static let tokenURL = URL(string: "https://oauth2.googleapis.com/token")!
  static let userInfoURL = URL(string: "https://openidconnect.googleapis.com/v1/userinfo")!

  private let config: GoogleOAuthConfig
  private let transport: any HTTPTransport
  private let opener: any URLOpening

  public init(config: GoogleOAuthConfig, transport: any HTTPTransport = URLSession.shared, opener: any URLOpening) {
    self.config = config
    self.transport = transport
    self.opener = opener
  }

  func authorizationURL(redirectURI: String, pkce: PKCE, state: String, loginHint: String?) -> URL {
    var parts = URLComponents(url: Self.authorizeURL, resolvingAgainstBaseURL: false)!
    var items = [
      URLQueryItem(name: "client_id", value: config.clientID),
      URLQueryItem(name: "redirect_uri", value: redirectURI),
      URLQueryItem(name: "response_type", value: "code"),
      URLQueryItem(name: "scope", value: Self.scopes.joined(separator: " ")),
      URLQueryItem(name: "code_challenge", value: pkce.challenge),
      URLQueryItem(name: "code_challenge_method", value: "S256"),
      URLQueryItem(name: "state", value: state),
      URLQueryItem(name: "access_type", value: "offline"),
      URLQueryItem(name: "prompt", value: "consent"),
    ]
    if let loginHint { items.append(URLQueryItem(name: "login_hint", value: loginHint)) }
    parts.queryItems = items
    return parts.url!
  }

  public func signIn(loginHint: String? = nil, timeout: TimeInterval = 300) async throws -> GoogleSignInResult {
    let listener = try await LoopbackListener.start()
    defer { listener.close() }
    let pkce = PKCE.generate()
    let state = PKCE.generate().verifier
    let url = authorizationURL(
      redirectURI: listener.redirectURI, pkce: pkce, state: state, loginHint: loginHint)
    guard await opener.open(url) else { throw GoogleSignInError.cancelled }
    let redirect = try await listener.waitForRedirect(timeout: timeout)
    let items = URLComponents(url: redirect, resolvingAgainstBaseURL: false)?.queryItems ?? []
    func value(_ name: String) -> String? { items.first { $0.name == name }?.value }
    guard value("state") == state else { throw GoogleSignInError.stateMismatch }
    if let error = value("error") { throw error == "access_denied" ? GoogleSignInError.cancelled : .denied(error) }
    guard let code = value("code") else { throw GoogleSignInError.transport }
    let token = try await exchange(code: code, verifier: pkce.verifier, redirectURI: listener.redirectURI)
    let missing = Self.scopes.dropFirst(2).filter { !token.scope.split(separator: " ").contains(Substring($0)) }
    guard missing.isEmpty else { throw GoogleSignInError.missingScopes(Array(missing)) }
    guard let refresh = token.refreshToken, !refresh.isEmpty else { throw GoogleSignInError.noRefreshToken }
    return GoogleSignInResult(refreshToken: refresh, email: try await email(accessToken: token.accessToken))
  }

  private struct TokenResponse: Decodable {
    let accessToken: String
    let refreshToken: String?
    let scope: String
  }

  private func exchange(code: String, verifier: String, redirectURI: String) async throws -> TokenResponse {
    var request = URLRequest(url: Self.tokenURL)
    request.httpMethod = "POST"
    request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
    var form = URLComponents()
    form.queryItems = [
      URLQueryItem(name: "grant_type", value: "authorization_code"),
      URLQueryItem(name: "code", value: code),
      URLQueryItem(name: "client_id", value: config.clientID),
      URLQueryItem(name: "client_secret", value: config.clientSecret),
      URLQueryItem(name: "code_verifier", value: verifier),
      URLQueryItem(name: "redirect_uri", value: redirectURI),
    ]
    request.httpBody = Data((form.percentEncodedQuery ?? "").utf8)
    let (data, response): (Data, HTTPURLResponse)
    do { (data, response) = try await transport.send(request) } catch { throw GoogleSignInError.transport }
    guard response.statusCode == 200 else { throw GoogleSignInError.exchangeFailed(response.statusCode) }
    do { return try JSONDecoder.api.decode(TokenResponse.self, from: data) } catch { throw GoogleSignInError.transport }
  }

  private func email(accessToken: String) async throws -> String {
    var request = URLRequest(url: Self.userInfoURL)
    request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    let (data, response): (Data, HTTPURLResponse)
    do { (data, response) = try await transport.send(request) } catch { throw GoogleSignInError.transport }
    guard response.statusCode == 200,
      let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let email = object["email"] as? String, !email.isEmpty
    else { throw GoogleSignInError.transport }
    return email
  }
}
