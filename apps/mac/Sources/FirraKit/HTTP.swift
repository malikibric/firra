import Foundation

/// The `/v1` error model: `{ error, message, request_id }`.
public struct APIErrorBody: Decodable, Sendable, Equatable {
  public let error: String
  public let message: String
  public let requestId: String
}

public enum APIError: Error, Sendable, Equatable {
  /// The server answered with the error model.
  case server(status: Int, code: String, message: String, requestID: String)
  /// The engine rejected the current handshake token.
  case unauthenticated
  /// Anything that is not a well-formed answer (offline, bad JSON, unexpected status).
  case transport(String)

  /// Text for the user; never contains tokens or row data.
  public var userMessage: String {
    switch self {
    case .server(_, let code, let message, _):
      return code == "reads_not_configured" ? "This server is not set up for that yet." : message
    case .unauthenticated: return "Firra is restarting…"
    case .transport: return "Could not reach Firra. Restart the engine and try again."
    }
  }
}

/// Anything that can send a request: `URLSession` in the app, a stub in tests.
public protocol HTTPTransport: Sendable {
  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

extension URLSession: HTTPTransport {
  public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    let (data, response) = try await data(for: request)
    guard let http = response as? HTTPURLResponse else {
      throw APIError.transport("not an HTTP response")
    }
    return (data, http)
  }
}

extension JSONDecoder {
  static let api: JSONDecoder = {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return decoder
  }()
}

extension JSONEncoder {
  static let api: JSONEncoder = {
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    return encoder
  }()
}
