import Foundation

/// One JSON line on the app <-> engine control socket. Mirrors `packages/contracts/schemas/local/handshake.json`
/// (docs/plans/local/contracts.md section 2); the shared contract tests keep the two sides in agreement.
public struct HandshakeMessage: Codable, Sendable, Equatable {
  public var type: String
  public var protocolVersion: Int?
  public var appVersion: String?
  public var engineVersion: String?
  public var name: String?
  public var value: String?
  public var apiPort: Int?
  public var apiToken: String?
  public var event: String?
  public var connectionId: String?
  public var code: String?

  enum CodingKeys: String, CodingKey {
    case type
    case protocolVersion = "protocol"
    case appVersion = "app_version"
    case engineVersion = "engine_version"
    case name, value
    case apiPort = "api_port"
    case apiToken = "api_token"
    case event
    case connectionId = "connection_id"
    case code
  }

  public init(type: String) { self.type = type }

  public static func hello(appVersion: String) -> HandshakeMessage {
    var m = HandshakeMessage(type: "hello")
    m.protocolVersion = 1
    m.appVersion = appVersion
    return m
  }

  public static func secretSet(name: String, value: String) -> HandshakeMessage {
    var m = HandshakeMessage(type: "secret_set")
    m.name = name
    m.value = value
    return m
  }

  public static func secretDelete(name: String) -> HandshakeMessage {
    var m = HandshakeMessage(type: "secret_delete")
    m.name = name
    return m
  }

  public static let secretsComplete = HandshakeMessage(type: "secrets_complete")
  public static let ping = HandshakeMessage(type: "ping")
  public static let shutdown = HandshakeMessage(type: "shutdown")
}

/// Slot names the engine accepts (the contract's pattern); the app never sends anything else.
public enum SecretSlot {
  public static let databaseKey = "db_encryption_key"
  public static let deviceKey = "device_signing_key"
  public static func googleRefreshToken(_ connection: String) -> String { "google_refresh_token:\(connection.lowercased())" }
  public static func slackUserToken(_ connection: String) -> String { "slack_user_token:\(connection)" }

  public static func isValid(_ name: String) -> Bool {
    if name == databaseKey || name == deviceKey { return true }
    for prefix in ["google_refresh_token:", "slack_user_token:"] where name.hasPrefix(prefix) {
      let id = name.dropFirst(prefix.count)
      return (1...64).contains(id.count)
        && id.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }
    }
    return false
  }
}

public struct EngineEndpoint: Sendable, Equatable {
  public let port: Int
  public let token: String
  public var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }
  public init(port: Int, token: String) {
    self.port = port
    self.token = token
  }
}

public enum EngineError: Error, Equatable {
  case socket(String)
  case timeout(String)
  case refused(String)  // the engine answered `error` with a class name
  case protocolViolation(String)
  case installFailed(String)
  case launchFailed(String)
}
