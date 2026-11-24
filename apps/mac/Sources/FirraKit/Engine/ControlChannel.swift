import Darwin
import Foundation

/// A connected unix-domain stream socket speaking one JSON line per message. Blocking calls with deadlines;
/// call them from a detached task, never from the main actor.
public final class ControlChannel: @unchecked Sendable {
  private var fd: Int32
  private var buffer = Data()
  private let lock = NSLock()
  static let maxLine = 16 * 1024

  init(fd: Int32) { self.fd = fd }

  deinit { close() }

  static func address(for path: String) throws -> sockaddr_un {
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(path.utf8)
    guard bytes.count < MemoryLayout.size(ofValue: addr.sun_path) else {
      throw EngineError.socket("socket path too long")
    }
    withUnsafeMutableBytes(of: &addr.sun_path) { raw in
      for (index, byte) in bytes.enumerated() { raw[index] = byte }
      raw[bytes.count] = 0
    }
    return addr
  }

  /// Connects, retrying until the engine has created the socket or the deadline passes.
  public static func connect(path: String, timeout: TimeInterval) throws -> ControlChannel {
    let deadline = Date().addingTimeInterval(timeout)
    var addr = try address(for: path)
    while true {
      let fd = socket(AF_UNIX, SOCK_STREAM, 0)
      guard fd >= 0 else { throw EngineError.socket("socket() failed") }
      let result = withUnsafePointer(to: &addr) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
          Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
      }
      if result == 0 {
        var one: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
        return ControlChannel(fd: fd)
      }
      Darwin.close(fd)
      if Date() >= deadline { throw EngineError.timeout("control socket not available") }
      Thread.sleep(forTimeInterval: 0.05)
    }
  }

  public func send(_ message: HandshakeMessage) throws {
    var line = try JSONEncoder().encode(message)
    line.append(0x0A)
    guard line.count <= Self.maxLine else { throw EngineError.protocolViolation("message too large") }
    try lock.withLock {
      guard fd >= 0 else { throw EngineError.socket("closed") }
      var sent = 0
      try line.withUnsafeBytes { raw in
        while sent < line.count {
          let n = write(fd, raw.baseAddress! + sent, line.count - sent)
          if n <= 0 { throw EngineError.socket("write failed") }
          sent += n
        }
      }
    }
  }

  /// Reads one message; nil when the peer closed the connection. Throws `.timeout` when nothing arrives in time.
  public func receive(timeout: TimeInterval) throws -> HandshakeMessage? {
    let deadline = Date().addingTimeInterval(timeout)
    while true {
      if let newline = buffer.firstIndex(of: 0x0A) {
        let line = buffer[buffer.startIndex..<newline]
        buffer.removeSubrange(buffer.startIndex...newline)
        do { return try JSONDecoder().decode(HandshakeMessage.self, from: Data(line)) } catch {
          throw EngineError.protocolViolation("undecodable message")
        }
      }
      if buffer.count > Self.maxLine { throw EngineError.protocolViolation("line too long") }
      let remaining = deadline.timeIntervalSinceNow
      if remaining <= 0 { throw EngineError.timeout("no message") }
      var poller = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
      let ready = poll(&poller, 1, Int32(min(remaining, 3600) * 1000))
      if ready < 0 { if errno == EINTR { continue }; throw EngineError.socket("poll failed") }
      if ready == 0 { continue }
      var chunk = [UInt8](repeating: 0, count: 4096)
      let n = read(fd, &chunk, chunk.count)
      if n == 0 { return nil }
      if n < 0 { if errno == EINTR || errno == EAGAIN { continue }; throw EngineError.socket("read failed") }
      buffer.append(contentsOf: chunk[0..<n])
    }
  }

  public func close() {
    lock.withLock {
      if fd >= 0 {
        Darwin.close(fd)
        fd = -1
      }
    }
  }
}
