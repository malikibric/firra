import Foundation
import Network

/// A one-shot HTTP listener on `127.0.0.1` (random port) that receives the OAuth redirect of a desktop client
/// (RFC 8252 section 7.3). It answers the browser with a short page and hands the request target to the caller.
public final class LoopbackListener: @unchecked Sendable {
  public private(set) var port: UInt16 = 0
  private let listener: NWListener
  private let queue = DispatchQueue(label: "app.firra.loopback")
  private let lock = NSLock()
  private var waiter: CheckedContinuation<URL, Error>?
  private var received: URL?
  private let path: String

  private init(listener: NWListener, path: String) {
    self.listener = listener
    self.path = path
  }

  public static func start(path: String = "/oauth2/callback") async throws -> LoopbackListener {
    let parameters = NWParameters.tcp
    parameters.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: .any)
    let listener = try NWListener(using: parameters)
    let instance = LoopbackListener(listener: listener, path: path)
    listener.newConnectionHandler = { instance.handle($0) }
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
      let once = Once()
      listener.stateUpdateHandler = { state in
        switch state {
        case .ready: if once.first() { continuation.resume() }
        case .failed(let error): if once.first() { continuation.resume(throwing: error) }
        case .cancelled: if once.first() { continuation.resume(throwing: GoogleSignInError.transport) }
        default: break
        }
      }
      listener.start(queue: instance.queue)
    }
    guard let port = listener.port?.rawValue else { throw GoogleSignInError.transport }
    instance.port = port
    return instance
  }

  public var redirectURI: String { "http://127.0.0.1:\(port)\(path)" }

  /// The first redirect request (path and query as a URL), or `GoogleSignInError.timeout`.
  public func waitForRedirect(timeout: TimeInterval) async throws -> URL {
    try await withThrowingTaskGroup(of: URL.self) { group in
      group.addTask { try await self.nextRedirect() }
      group.addTask {
        try await Task.sleep(for: .seconds(timeout))
        throw GoogleSignInError.timeout
      }
      defer { group.cancelAll() }
      return try await group.next()!
    }
  }

  private func nextRedirect() async throws -> URL {
    try await withTaskCancellationHandler {
      try await withCheckedThrowingContinuation { continuation in
        lock.withLock {
          if let received {
            continuation.resume(returning: received)
          } else {
            waiter = continuation
          }
        }
      }
    } onCancel: {
      lock.withLock {
        waiter?.resume(throwing: CancellationError())
        waiter = nil
      }
    }
  }

  public func close() { listener.cancel() }

  private func deliver(_ url: URL) {
    lock.withLock {
      guard received == nil else { return }
      received = url
      waiter?.resume(returning: url)
      waiter = nil
    }
  }

  private func handle(_ connection: NWConnection) {
    connection.start(queue: queue)
    connection.receive(minimumIncompleteLength: 1, maximumLength: 8192) { [self] data, _, _, _ in
      guard let data, let request = String(data: data, encoding: .utf8) else {
        connection.cancel()
        return
      }
      let target = request.split(separator: "\r\n").first?.split(separator: " ").dropFirst().first.map(String.init)
      guard let target, let url = URL(string: "http://127.0.0.1" + target), url.path == path else {
        Self.respond(connection, status: "404 Not Found", body: "Not found")
        return
      }
      Self.respond(
        connection, status: "200 OK",
        body: "<html><body style=\"font-family:-apple-system\"><h3>Firra</h3>You can close this tab and return to Firra.</body></html>"
      )
      deliver(url)
    }
  }

  private static func respond(_ connection: NWConnection, status: String, body: String) {
    let payload = Data(body.utf8)
    let head =
      "HTTP/1.1 \(status)\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: \(payload.count)\r\nConnection: close\r\n\r\n"
    // Close only after the answer has been written, or the browser sees a broken connection.
    connection.send(content: Data(head.utf8) + payload, completion: .contentProcessed { _ in connection.cancel() })
  }
}

private final class Once: @unchecked Sendable {
  private let lock = NSLock()
  private var done = false
  func first() -> Bool { lock.withLock { if done { return false }; done = true; return true } }
}
