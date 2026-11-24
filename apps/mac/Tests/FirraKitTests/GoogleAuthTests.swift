import Foundation
import Testing
@testable import FirraKit

/// Pretends to be the browser: reads the authorization URL and calls the loopback redirect.
final class FakeBrowser: URLOpening, @unchecked Sendable {
  enum Behaviour: Sendable {
    case approve  // redirect with a code and the right state
    case approveWithWrongState
    case deny(String)
    case refuseToOpen
    case never
  }
  private let behaviour: Behaviour
  private let lock = NSLock()
  private var _opened: [URL] = []
  init(_ behaviour: Behaviour) { self.behaviour = behaviour }
  var opened: [URL] { lock.withLock { _opened } }

  func open(_ url: URL) async -> Bool {
    lock.withLock { _opened.append(url) }
    let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
    func value(_ name: String) -> String { query.first { $0.name == name }?.value ?? "" }
    let redirect = value("redirect_uri")
    var parts = URLComponents(string: redirect)!
    switch behaviour {
    case .refuseToOpen: return false
    case .never: return true
    case .approve: parts.queryItems = [.init(name: "code", value: "auth-code-1"), .init(name: "state", value: value("state"))]
    case .approveWithWrongState: parts.queryItems = [.init(name: "code", value: "c"), .init(name: "state", value: "forged")]
    case .deny(let error): parts.queryItems = [.init(name: "error", value: error), .init(name: "state", value: value("state"))]
    }
    let target = parts.url!
    Task.detached { _ = try? await URLSession.shared.data(from: target) }
    return true
  }
}

private let config = GoogleOAuthConfig(clientID: "cid.apps.googleusercontent.com", clientSecret: "GOCSPX-test")

private func tokenReply(refresh: String? = "1//refresh", scope: String? = nil) -> StubTransport.Reply {
  let granted = scope ?? GoogleDesktopAuth.scopes.joined(separator: " ")
  let refreshField = refresh.map { #""refresh_token":"\#($0)","# } ?? ""
  return .init(status: 200, body: #"{"access_token":"ya29.a","expires_in":3599,\#(refreshField)"scope":"\#(granted)","token_type":"Bearer"}"#)  // nosemgrep
}
private let userinfo = StubTransport.Reply(status: 200, body: #"{"email":"ana@example.com","sub":"1"}"#)

@Suite(.serialized) struct GoogleDesktopAuthTests {
  @Test func signsInThroughTheLoopbackAndReturnsTheRefreshTokenAndEmail() async throws {
    let transport = StubTransport([tokenReply(), userinfo])
    let browser = FakeBrowser(.approve)
    let result = try await GoogleDesktopAuth(config: config, transport: transport, opener: browser)
      .signIn(loginHint: "ana@example.com", timeout: 10)
    #expect(result == GoogleSignInResult(refreshToken: "1//refresh", email: "ana@example.com"))

    // The authorization URL: loopback redirect, PKCE S256, offline access, consent, state, only read scopes.
    let url = try #require(browser.opened.first)
    let items = Dictionary(uniqueKeysWithValues: (URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []).map { ($0.name, $0.value ?? "") })
    #expect(url.host == "accounts.google.com")
    #expect(items["redirect_uri"]?.hasPrefix("http://127.0.0.1:") == true)
    #expect(items["code_challenge_method"] == "S256" && (items["code_challenge"]?.count ?? 0) >= 43)
    #expect(items["access_type"] == "offline" && items["prompt"] == "consent" && items["response_type"] == "code")
    #expect(items["login_hint"] == "ana@example.com" && (items["state"]?.count ?? 0) >= 32)
    let scopes = Set((items["scope"] ?? "").split(separator: " ").map(String.init))
    #expect(scopes == ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/calendar.readonly"])

    // The token request carries the code, the verifier and the same redirect URI, never in the URL.
    let exchange = transport.requests[0]
    #expect(exchange.url?.absoluteString == "https://oauth2.googleapis.com/token" && exchange.httpMethod == "POST")
    let form = String(data: exchange.httpBody ?? Data(), encoding: .utf8) ?? ""
    #expect(form.contains("grant_type=authorization_code") && form.contains("code=auth-code-1"))
    #expect(form.contains("code_verifier=") && form.contains("redirect_uri=http"))
    #expect(transport.requests[1].value(forHTTPHeaderField: "Authorization") == "Bearer ya29.a")  // nosemgrep
  }

  @Test func aForgedStateIsRejectedBeforeAnythingIsExchanged() async throws {
    let transport = StubTransport([tokenReply(), userinfo])
    await #expect(throws: GoogleSignInError.stateMismatch) {
      try await GoogleDesktopAuth(config: config, transport: transport, opener: FakeBrowser(.approveWithWrongState))
        .signIn(timeout: 10)
    }
    #expect(transport.requests.isEmpty)
  }

  @Test func denyingConsentIsACancellation() async throws {
    await #expect(throws: GoogleSignInError.cancelled) {
      try await GoogleDesktopAuth(config: config, transport: StubTransport([]), opener: FakeBrowser(.deny("access_denied")))
        .signIn(timeout: 10)
    }
    await #expect(throws: GoogleSignInError.denied("invalid_scope")) {
      try await GoogleDesktopAuth(config: config, transport: StubTransport([]), opener: FakeBrowser(.deny("invalid_scope")))
        .signIn(timeout: 10)
    }
  }

  @Test func aBrowserThatCannotOpenOrNeverAnswersEnds() async throws {
    await #expect(throws: GoogleSignInError.cancelled) {
      try await GoogleDesktopAuth(config: config, transport: StubTransport([]), opener: FakeBrowser(.refuseToOpen)).signIn(timeout: 5)
    }
    await #expect(throws: GoogleSignInError.timeout) {
      try await GoogleDesktopAuth(config: config, transport: StubTransport([]), opener: FakeBrowser(.never)).signIn(timeout: 0.3)
    }
  }

  @Test func aMissingRefreshTokenOrScopeIsAnError() async throws {
    await #expect(throws: GoogleSignInError.noRefreshToken) {
      try await GoogleDesktopAuth(config: config, transport: StubTransport([tokenReply(refresh: nil), userinfo]), opener: FakeBrowser(.approve))
        .signIn(timeout: 10)
    }
    let narrow = "openid email https://www.googleapis.com/auth/calendar.readonly"
    await #expect(throws: GoogleSignInError.missingScopes(["https://www.googleapis.com/auth/gmail.readonly"])) {
      try await GoogleDesktopAuth(config: config, transport: StubTransport([tokenReply(scope: narrow), userinfo]), opener: FakeBrowser(.approve))
        .signIn(timeout: 10)
    }
  }

  @Test func googleErrorsAreSurfacedWithoutTheirBodies() async throws {
    await #expect(throws: GoogleSignInError.exchangeFailed(400)) {
      try await GoogleDesktopAuth(
        config: config, transport: StubTransport([.init(status: 400, body: #"{"error":"invalid_grant","error_description":"secret detail"}"#)]),
        opener: FakeBrowser(.approve)
      ).signIn(timeout: 10)
    }
  }
}

@Suite struct LoopbackListenerTests {
  @Test func onlyTheCallbackPathIsAcceptedAndThePortIsLoopback() async throws {
    let listener = try await LoopbackListener.start()
    defer { listener.close() }
    #expect(listener.port > 1023 && listener.redirectURI.hasPrefix("http://127.0.0.1:"))
    let other = URL(string: "http://127.0.0.1:\(listener.port)/favicon.ico")!
    let (_, response) = try await URLSession.shared.data(from: other)
    #expect((response as? HTTPURLResponse)?.statusCode == 404)
    let hit = URL(string: "http://127.0.0.1:\(listener.port)/oauth2/callback?code=abc&state=xyz")!
    let (data, ok) = try await URLSession.shared.data(from: hit)
    #expect((ok as? HTTPURLResponse)?.statusCode == 200 && String(data: data, encoding: .utf8)?.contains("close this tab") == true)
    let redirect = try await listener.waitForRedirect(timeout: 5)
    #expect(redirect.query == "code=abc&state=xyz")
  }
}
