import Foundation
@testable import FirraKit

/// Records requests and answers from a script, so no test touches the network.
final class StubTransport: HTTPTransport, @unchecked Sendable {
  struct Reply { let status: Int; let body: String }
  private let lock = NSLock()
  private var replies: [Reply]
  private(set) var requests: [URLRequest] = []

  init(_ replies: [Reply]) { self.replies = replies }

  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    let reply: Reply = lock.withLock {
      requests.append(request)
      return replies.isEmpty ? Reply(status: 500, body: "{}") : replies.removeFirst()
    }
    let response = HTTPURLResponse(
      url: request.url!, statusCode: reply.status, httpVersion: nil, headerFields: nil)!
    return (Data(reply.body.utf8), response)
  }
}

/// Holds every request until the test releases it, so a test can switch account while one is in flight.
final class GatedTransport: HTTPTransport, @unchecked Sendable {
  private let lock = NSLock()
  private var waiting: [CheckedContinuation<Void, Never>] = []
  private(set) var requests: [URLRequest] = []
  private let reply: StubTransport.Reply
  init(reply: StubTransport.Reply) { self.reply = reply }

  func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    lock.withLock { requests.append(request) }
    await withCheckedContinuation { continuation in lock.withLock { waiting.append(continuation) } }
    let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: nil, headerFields: nil)!
    return (Data(reply.body.utf8), response)
  }

  var requestCount: Int { lock.withLock { requests.count } }

  /// Waits until at least `count` requests are being held.
  func waitForRequests(_ count: Int) async {
    while lock.withLock({ requests.count }) < count { try? await Task.sleep(for: .milliseconds(5)) }
  }

  func releaseAll() {
    let all = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
      defer { waiting = [] }
      return waiting
    }
    for continuation in all { continuation.resume() }
  }
}

let testBaseURL = URL(string: "http://127.0.0.1:49152")!
