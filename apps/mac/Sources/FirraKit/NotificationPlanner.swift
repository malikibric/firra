import Foundation

@MainActor
public protocol ShownCardStore: AnyObject {
  var ids: [String] { get set }
}

/// Only card ids are persisted, separately for each signed-in user on this Mac.
@MainActor
public final class UserDefaultsShownCardStore: ShownCardStore {
  private let defaults: UserDefaults
  private let key: String

  public init(userID: String, defaults: UserDefaults = .standard) {
    self.defaults = defaults
    self.key = "notifications.shownCards.\(userID)"
  }

  public var ids: [String] {
    get { defaults.stringArray(forKey: key) ?? [] }
    set { defaults.set(newValue, forKey: key) }
  }
}

/// Selection of server-important cards in engine order, with permanent once-per-card memory.
@MainActor
public struct NotificationPlanner {
  private let store: any ShownCardStore

  public init(store: any ShownCardStore) { self.store = store }

  public func plan(cards: [DecisionItem], focusedClientIDs: Set<String> = [],
                   focusedDecisionIDs: Set<String> = []) -> [DecisionItem] {
    var remembered = store.ids
    var known = Set(remembered)
    var result: [DecisionItem] = []
    for card in cards where card.importance == "important" && card.status == "new" &&
      !focusedClientIDs.contains(card.clientId) && !focusedDecisionIDs.contains(card.id) &&
      !known.contains(card.id) {
      result.append(card)
      remembered.append(card.id)
      known.insert(card.id)
    }
    store.ids = remembered
    return result
  }
}

/// Local v1 never falls back to assessment notifications.
public enum NotificationRoute: Sendable, Equatable {
  case decisions([DecisionItem])
  public static func load(api: APIClient, workspaceId: String) async throws -> Self {
    .decisions(try await api.decisions(workspaceId: workspaceId, importance: "important").items)
  }
}
