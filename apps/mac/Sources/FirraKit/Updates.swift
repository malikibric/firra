import Foundation
import Observation

/// One entry of `latest.json` on the release bucket (plan L6-02): the DMG address and its SHA-256.
public struct UpdateRelease: Decodable, Sendable, Equatable {
  public let version: String
  public let url: URL
  public let sha256: String
  public let notes: String?

  /// Only well-formed, https, hex-hash entries are accepted; anything else is ignored.
  public var isValid: Bool {
    url.scheme == "https" && url.host != nil
      && version.range(of: #"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$"#, options: .regularExpression) != nil
      && sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil
  }
}

public enum VersionOrder {
  /// `1.2.10` > `1.2.9`; a pre-release (`1.0.0-pilot`) sorts before the same release number.
  public static func isNewer(_ candidate: String, than current: String) -> Bool {
    func parts(_ v: String) -> (core: [Int], pre: Bool)? {
      let pieces = v.split(separator: "-", maxSplits: 1)
      guard let head = pieces.first else { return nil }
      let numbers = head.split(separator: ".").compactMap { Int($0) }
      guard numbers.count == 3, numbers.count == head.split(separator: ".").count else { return nil }
      return (numbers, pieces.count == 2)
    }
    guard let a = parts(candidate), let b = parts(current) else { return false }
    if a.core != b.core { return a.core.lexicographicallyPrecedes(b.core) == false }
    return b.pre && !a.pre
  }
}

/// Looks for a new version at most once a day and only reports it: nothing is downloaded or installed.
@MainActor @Observable public final class UpdateModel {
  public static let maxFeedBytes = 16 * 1024
  public private(set) var available: UpdateRelease?
  public private(set) var checkedAt: Date?
  public private(set) var error: String?
  private let feed: URL?
  private let current: String
  private let fetch: @Sendable (URL) async throws -> Data
  private let now: @Sendable () -> Date

  /// `feed == nil` (no release address configured yet) turns the check off.
  public init(
    feed: URL?, currentVersion: String, lastChecked: Date? = nil,
    fetch: @escaping @Sendable (URL) async throws -> Data,
    now: @escaping @Sendable () -> Date = { Date() }
  ) {
    self.feed = feed.flatMap { $0.scheme == "https" ? $0 : nil }
    self.current = currentVersion
    self.checkedAt = lastChecked
    self.fetch = fetch
    self.now = now
  }

  public var enabled: Bool { feed != nil }
  public var verifyHint: String? {
    available.map { "After downloading, check it with: shasum -a 256 <file>  (expected \($0.sha256))" }
  }

  public func checkIfDue() async {
    guard let feed else { return }
    if let checkedAt, now().timeIntervalSince(checkedAt) < 24 * 3600 { return }
    do {
      let data = try await fetch(feed)
      checkedAt = now(); error = nil
      guard data.count <= Self.maxFeedBytes,
        let release = try? JSONDecoder().decode(UpdateRelease.self, from: data), release.isValid
      else { available = nil; error = "The update information was not valid."; return }
      available = VersionOrder.isNewer(release.version, than: current) ? release : nil
    } catch {
      self.error = "Could not check for updates."
    }
  }
}
