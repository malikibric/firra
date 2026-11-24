import Foundation

/// Which clients got worse since the last check, for a native notification. Only rises into elevated
/// or high count; the first check of a session is the baseline and never notifies.
public struct AttentionChanges: Sendable {
  private var previous: [String: String]?

  public init() {}

  static func rank(_ state: String) -> Int {
    switch state {
    case "high": 3
    case "elevated": 2
    case "watch": 1
    default: 0
    }
  }

  /// Records the current list and returns the clients that rose into elevated or high.
  public mutating func escalations(in items: [AttentionItem]) -> [AttentionItem] {
    let current = Dictionary(items.map { ($0.clientId, $0.riskState) }, uniquingKeysWith: { first, _ in first })
    defer { previous = current }
    guard let previous else { return [] }
    return items.filter { item in
      let now = Self.rank(item.riskState)
      return now >= 2 && now > Self.rank(previous[item.clientId] ?? "stable")
    }
  }
}
