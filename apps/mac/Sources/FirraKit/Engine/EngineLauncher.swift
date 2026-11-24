import Foundation

/// A running engine process.
public protocol RunningEngine: Sendable {
  func terminate()
  func kill()
  /// Resolves when the process has exited.
  func waitUntilExit() async
}

public struct EngineLaunchRequest: Sendable, Equatable {
  public let layout: EngineLayout
  public let home: URL
  public let controlDirectory: URL
  /// Where the engine's stderr goes (0600, rewritten at every start). It holds log lines (ids, counts, error
  /// classes) and tracebacks, never secrets: the engine's logging rules forbid them.
  public var errorLog: URL? { home.appending(path: "log/engine-stderr.log") }
}

public protocol EngineLaunching: Sendable {
  func launch(_ request: EngineLaunchRequest) throws -> RunningEngine
}

/// Starts the bundled Python as a child of the app with a **minimal, explicit environment**: no secrets, no
/// inherited variables. `PATH` also lists the places where a user's AI command-line tool is usually installed.
public struct ProcessEngineLauncher: EngineLaunching {
  public init() {}

  public func launch(_ request: EngineLaunchRequest) throws -> RunningEngine {
    let process = Process()
    process.executableURL = request.layout.python
    process.arguments = [
      "-I", "-B", "-m", "firra.local",
      "--home", request.home.path,
      "--pg-bin", request.layout.postgresBin.path,
      "--control-dir", request.controlDirectory.path,
    ]
    let home = NSHomeDirectory()
    process.environment = [
      "HOME": home,
      "USER": NSUserName(),
      "LANG": "en_US.UTF-8",
      "LC_ALL": "en_US.UTF-8",
      "TMPDIR": NSTemporaryDirectory(),
      "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:\(home)/.local/bin",
    ]
    process.currentDirectoryURL = request.home
    process.standardInput = FileHandle.nullDevice
    // The engine logs to stdout and tracebacks go to stderr: both land in the one private log file.
    let log = Self.openErrorLog(request.errorLog)
    process.standardOutput = log ?? FileHandle.nullDevice
    process.standardError = log ?? FileHandle.nullDevice
    do { try process.run() } catch { throw EngineError.launchFailed("could not start the engine") }
    return ProcessHandle(process)
  }

  static func openErrorLog(_ url: URL?) -> FileHandle? {
    guard let url else { return nil }
    let directory = url.deletingLastPathComponent()
    try? FileManager.default.createDirectory(
      at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    let fd = open(url.path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0o600)
    return fd < 0 ? nil : FileHandle(fileDescriptor: fd, closeOnDealloc: true)
  }

  private final class ProcessHandle: RunningEngine, @unchecked Sendable {
    let process: Process
    init(_ process: Process) { self.process = process }
    func terminate() { process.terminate() }
    func kill() { Darwin.kill(process.processIdentifier, SIGKILL) }
    func waitUntilExit() async {
      await withCheckedContinuation { continuation in
        DispatchQueue.global().async {
          self.process.waitUntilExit()
          continuation.resume()
        }
      }
    }
  }
}
