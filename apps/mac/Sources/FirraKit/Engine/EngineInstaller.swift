import Darwin
import Foundation

/// Where the engine runs from once installed.
public struct EngineLayout: Sendable, Equatable {
  public let root: URL
  public var python: URL { root.appending(path: "python/bin/python3.12") }
  public var postgresBin: URL { root.appending(path: "postgres/bin") }
}

public protocol EngineInstalling: Sendable {
  func install() throws -> EngineLayout
}

/// R1 fallback of the packaging spike (docs/research/engine-bundle.md): a downloaded, ad-hoc signed app carries
/// its engine under quarantine and macOS stalls on first launch of those binaries. So the engine is **copied**
/// out of the app bundle into Application Support and quarantine is cleared on the copy, which needs no admin
/// rights. The copy is keyed by `buildID`; older copies are removed.
public struct EngineInstaller: EngineInstalling {
  public let bundledEngine: URL
  public let home: URL
  public let buildID: String

  public init(bundledEngine: URL, home: URL, buildID: String) {
    self.bundledEngine = bundledEngine
    self.home = home
    self.buildID = buildID
  }

  var installRoot: URL { home.appending(path: "engine") }
  var target: URL { installRoot.appending(path: buildID) }

  public func install() throws -> EngineLayout {
    let fm = FileManager.default
    guard fm.fileExists(atPath: bundledEngine.appending(path: "python/bin/python3.12").path) else {
      throw EngineError.installFailed("bundled engine missing")
    }
    try fm.createDirectory(at: installRoot, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    let marker = target.appending(path: ".complete")
    if !fm.fileExists(atPath: marker.path) {
      let staging = installRoot.appending(path: ".staging-\(UUID().uuidString)")
      do {
        try fm.copyItem(at: bundledEngine, to: staging)
        try Self.clearQuarantine(at: staging)
        try Data().write(to: staging.appending(path: ".complete"))
        try? fm.removeItem(at: target)
        try fm.moveItem(at: staging, to: target)
      } catch {
        try? fm.removeItem(at: staging)
        throw EngineError.installFailed("copy failed")
      }
    }
    for entry in (try? fm.contentsOfDirectory(at: installRoot, includingPropertiesForKeys: nil)) ?? []
    where entry.lastPathComponent != buildID {
      try? fm.removeItem(at: entry)
    }
    return EngineLayout(root: target)
  }

  /// Removes `com.apple.quarantine` from every file and directory under `url`.
  static func clearQuarantine(at url: URL) throws {
    let fm = FileManager.default
    var paths = [url.path]
    if let walker = fm.enumerator(atPath: url.path) {
      for case let relative as String in walker { paths.append(url.appending(path: relative).path) }
    }
    for path in paths {
      let status = removexattr(path, "com.apple.quarantine", XATTR_NOFOLLOW)
      if status != 0 && errno != ENOATTR && errno != ENOENT && errno != EPERM {
        throw EngineError.installFailed("could not clear quarantine")
      }
    }
  }
}
