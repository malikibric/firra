import Foundation
import Observation

@MainActor @Observable
public final class AttentionModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var items: [AttentionItem] = []
  /// True while the list on screen comes from the local copy and could not be refreshed.
  public private(set) var isOffline = false

  private let api: APIClient
  private let cache: any ResponseCache
  public init(api: APIClient, cache: any ResponseCache = NoCache()) {
    self.api = api
    self.cache = cache
  }

  /// A client was restricted: drop it from what is shown, from memory and from the local copy.
  public func forget(clientId: String) {
    items.removeAll { $0.clientId == clientId }
  }

  /// The workspace the items on screen belong to.
  public private(set) var workspaceId: String?

  public func load(workspaceId: String) async {
    if self.workspaceId != workspaceId {
      // Another workspace: never show the previous one's clients while this one loads or fails.
      items = []
      isOffline = false
      self.workspaceId = workspaceId
    }
    let key = "attention-\(workspaceId)"
    if items.isEmpty, let data = cache.load(key),
      let cached = try? JSONDecoder.api.decode(AttentionResponse.self, from: data)
    {
      items = cached.items
      phase = .ready
    } else if items.isEmpty {
      phase = .loading
    }
    do {
      let fresh = try await api.attention(workspaceId: workspaceId)
      if let data = try? JSONEncoder.api.encode(fresh) { cache.save(data, for: key) }
      items = fresh.items
      isOffline = false
      phase = .ready
    } catch {
      if !items.isEmpty {
        isOffline = true
        phase = .ready
      } else {
        phase = .failed((error as? APIError)?.userMessage ?? "Something went wrong.")
      }
    }
  }

  /// "3 clients need attention" for the menu bar.
  public var summary: String {
    if items.isEmpty {
      switch phase {
      case .idle, .loading: return "Checking your clients…"
      case .failed: return "Could not check your clients"
      case .ready: break
      }
    }
    let suffix = isOffline ? " (not refreshed)" : ""
    switch items.count {
    case 0: return "No client needs attention"
    case 1: return "1 client needs attention" + suffix
    case 0: return "No client needs attention"
    case 1: return "1 client needs attention"
    default: return "\(items.count) clients need attention" + suffix
    }
  }
}

/// Clients, their domains and data policies (owner and admin).
@MainActor @Observable
public final class ClientsModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var clients: [SetupClient] = []
  public private(set) var message: String?
  /// Called after a client became metadata only or excluded, so local copies of its content go too.
  public var onClientRestricted: (@MainActor (String) async -> Void)?

  private let api: APIClient
  public init(api: APIClient) { self.api = api }

  public func load(workspaceId: String) async {
    if clients.isEmpty { phase = .loading }
    do {
      clients = try await api.setupClients(workspaceId: workspaceId).items
      phase = .ready
    } catch {
      phase = clients.isEmpty ? .failed(Self.text(error)) : .ready
      message = Self.text(error)
    }
  }

  /// Splits "a.com, b.com" into clean lowercase domains; anything else is left for the server to refuse.
  public static func parseDomains(_ input: String) -> [String] {
    input.split(whereSeparator: { ", \n;".contains($0) })
      .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
      .filter { !$0.isEmpty }
  }

  public func add(workspaceId: String, name: String, domains: String, policy: ClientPolicy) async -> Bool {
    await run(workspaceId) {
      try await self.api.createClient(
        workspaceId: workspaceId, name: name.trimmingCharacters(in: .whitespaces),
        policy: policy, domains: Self.parseDomains(domains))
    }
  }

  public func rename(workspaceId: String, client: SetupClient, to name: String) async -> Bool {
    let trimmed = name.trimmingCharacters(in: .whitespaces)
    guard !trimmed.isEmpty, trimmed != client.name else { return true }
    return await run(workspaceId) {
      try await self.api.renameClient(
        workspaceId: workspaceId, clientId: client.id, name: trimmed, status: client.status)
    }
  }

  public func setPolicy(workspaceId: String, clientId: String, policy: ClientPolicy) async -> Bool {
    let done = await run(workspaceId) {
      try await self.api.setClientPolicy(workspaceId: workspaceId, clientId: clientId, policy: policy)
    }
    if done, policy != .full { await onClientRestricted?(clientId) }
    return done
  }

  public func setDomains(workspaceId: String, clientId: String, domains: String) async -> Bool {
    await run(workspaceId) {
      try await self.api.setClientDomains(
        workspaceId: workspaceId, clientId: clientId, domains: Self.parseDomains(domains))
    }
  }

  private func run(_ workspaceId: String, _ action: () async throws -> Void) async -> Bool {
    do {
      try await action()
      message = nil
      await load(workspaceId: workspaceId)
      return true
    } catch {
      message = Self.text(error)
      return false
    }
  }

  private static func text(_ error: Error) -> String {
    (error as? APIError)?.userMessage ?? "Something went wrong. Try again."
  }
}

/// The firm's service catalog: what Firra matches client needs against.
@MainActor @Observable
public final class ServicesModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var services: [ServiceItem] = []
  public private(set) var message: String?

  private let api: APIClient
  public init(api: APIClient) { self.api = api }

  public func load(workspaceId: String) async {
    if services.isEmpty { phase = .loading }
    do {
      services = try await api.services(workspaceId: workspaceId).items
      phase = .ready
    } catch {
      phase = services.isEmpty ? .failed(Self.text(error)) : .ready
      message = Self.text(error)
    }
  }

  /// Splits "SEO, paid ads" into clean lowercase keywords; the server refuses the rest.
  public static func parseKeywords(_ input: String) -> [String] {
    var seen = Set<String>()
    return input.split(whereSeparator: { $0 == "," || $0.isNewline })
      .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
      .filter { !$0.isEmpty && seen.insert($0).inserted }
  }

  public func save(
    workspaceId: String, serviceId: String?, name: String, description: String, keywords: String
  ) async -> Bool {
    let label = name.trimmingCharacters(in: .whitespaces)
    guard !label.isEmpty else { return false }
    return await run(workspaceId) {
      let parsed = Self.parseKeywords(keywords)
      if let serviceId {
        try await self.api.saveService(
          workspaceId: workspaceId, serviceId: serviceId, name: label,
          description: description.trimmingCharacters(in: .whitespaces), keywords: parsed)
      } else {
        try await self.api.createService(
          workspaceId: workspaceId, name: label,
          description: description.trimmingCharacters(in: .whitespaces), keywords: parsed)
      }
    }
  }

  public func delete(workspaceId: String, serviceId: String) async -> Bool {
    await run(workspaceId) {
      try await self.api.deleteService(workspaceId: workspaceId, serviceId: serviceId)
    }
  }

  private func run(_ workspaceId: String, _ action: () async throws -> Void) async -> Bool {
    do {
      try await action()
      message = nil
      await load(workspaceId: workspaceId)
      return true
    } catch {
      message = Self.text(error)
      return false
    }
  }

  private static func text(_ error: Error) -> String {
    (error as? APIError)?.userMessage ?? "Something went wrong. Try again."
  }
}

/// The audit log of the workspace, newest first, 50 rows per page.
@MainActor @Observable
public final class AuditModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var entries: [AuditEntry] = []
  public private(set) var nextPage: Int?
  public private(set) var loadMoreFailed = false

  private let api: APIClient
  public init(api: APIClient) { self.api = api }

  public func load(workspaceId: String) async {
    phase = .loading
    do {
      let first = try await api.audit(workspaceId: workspaceId)
      entries = first.items
      nextPage = first.nextPage
      phase = .ready
    } catch {
      phase = entries.isEmpty ? .failed((error as? APIError)?.userMessage ?? "Could not load.") : .ready
    }
  }

  public func loadMore(workspaceId: String) async {
    guard let page = nextPage else { return }
    do {
      let more = try await api.audit(workspaceId: workspaceId, page: page)
      entries += more.items
      nextPage = more.nextPage
      loadMoreFailed = false
    } catch {
      // Keep the page number: the list is not complete and the user can try again.
      loadMoreFailed = true
    }
  }
}

/// Replays of stored history: request one, list the runs, and read what Firra would have seen for
/// each client week by week.
@MainActor @Observable
public final class ReplayModel {
  public enum State: Equatable { case idle, sending, requested, failed(String) }
  public private(set) var state: State = .idle
  public private(set) var runs: [ReplayRunItem] = []
  public private(set) var clients: [ClientListItem] = []
  public private(set) var result: ReplayClientResult?
  public private(set) var message: String?

  private let api: APIClient
  public init(api: APIClient) { self.api = api }

  public func loadRuns(workspaceId: String) async {
    do {
      runs = try await api.replayRuns(workspaceId: workspaceId)
      if clients.isEmpty { clients = try await api.allClients(workspaceId: workspaceId) }
      message = nil
    } catch {
      message = (error as? APIError)?.userMessage ?? "Could not load the replays."
    }
  }

  public func open(workspaceId: String, runId: String, clientId: String) async {
    result = nil
    do {
      result = try await api.replayClient(workspaceId: workspaceId, runId: runId, clientId: clientId)
      message = nil
    } catch APIError.server(let status, _, _, _) where status == 404 {
      message = "This replay has no story for that client (it was not a client in that window)."
    } catch {
      message = (error as? APIError)?.userMessage ?? "Could not load this client's replay."
    }
  }

  static func day(_ date: Date) -> String {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(identifier: "UTC")!
    let parts = calendar.dateComponents([.year, .month, .day], from: date)
    return String(format: "%04d-%02d-%02d", parts.year!, parts.month!, parts.day!)
  }

  public func request(workspaceId: String, start: Date, end: Date, stepDays: Int) async {
    guard start <= end else {
      state = .failed("The start date must be before the end date.")
      return
    }
    state = .sending
    do {
      try await api.requestReplay(
        workspaceId: workspaceId, start: Self.day(start), end: Self.day(end), stepDays: stepDays)
      state = .requested
      await loadRuns(workspaceId: workspaceId)
    } catch {
      state = .failed((error as? APIError)?.userMessage ?? "Could not start the replay.")
    }
  }
}

/// One client: the assessment, why (drivers with their quotes), commitments, people and history.
@MainActor @Observable
public final class ClientDetailModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var detail: ClientDetail?
  public private(set) var evidence: [String: EvidenceItem] = [:]
  public private(set) var timeline: [TimelineEvent] = []
  public private(set) var timelineFailed = false
  public private(set) var canLoadMoreTimeline = false
  public private(set) var isLoadingTimeline = false
  public private(set) var message: String?

  private var timelineCursor: String?
  private var evidenceRequested: Set<String> = []
  private var generation = UUID()
  private let api: APIClient
  public init(api: APIClient) { self.api = api }

  public func load(workspaceId: String, clientId: String) async {
    reset()
    let generation = self.generation
    phase = .loading
    do {
      let detail = try await api.clientDetail(workspaceId: workspaceId, clientId: clientId)
      guard generation == self.generation else { return }
      self.detail = detail
      phase = .ready
    } catch {
      guard generation == self.generation else { return }
      phase = .failed((error as? APIError)?.userMessage ?? "Could not load this client.")
      return
    }
    isLoadingTimeline = true
    // The latest dated events; a failure leaves the rest of the page as it is.
    if let page = try? await api.clientTimeline(workspaceId: workspaceId, clientId: clientId) {
      guard generation == self.generation else { return }
      timeline = page.items
      timelineCursor = page.nextCursor
      canLoadMoreTimeline = timelineCursor != nil
      timelineFailed = false
    } else {
      guard generation == self.generation else { return }
      timeline = []
      timelineFailed = true
    }
    isLoadingTimeline = false
    // The quotes behind the drivers; a failure here leaves the facts visible without quotes.
    var cursor: String?
    for _ in 0..<3 {
      guard let page = try? await api.clientEvidence(workspaceId: workspaceId, clientId: clientId, cursor: cursor)
      else { break }
      guard generation == self.generation else { return }
      for item in page.items { evidence[item.id] = item }
      guard let next = page.nextCursor else { break }
      cursor = next
    }
  }

  public func loadMoreTimeline(workspaceId: String, clientId: String) async {
    guard !isLoadingTimeline, let cursor = timelineCursor else { return }
    let generation = self.generation
    isLoadingTimeline = true
    defer { if generation == self.generation { isLoadingTimeline = false } }
    do {
      let page = try await api.clientTimeline(workspaceId: workspaceId, clientId: clientId, cursor: cursor)
      guard generation == self.generation else { return }
      var ids = Set(timeline.map(\.id))
      timeline.append(contentsOf: page.items.filter { ids.insert($0.id).inserted })
      timelineCursor = page.nextCursor
      canLoadMoreTimeline = timelineCursor != nil
      timelineFailed = false
    } catch {
      guard generation == self.generation else { return }
      timelineFailed = true
    }
  }

  public func quotes(for event: TimelineEvent) -> [EvidenceItem] {
    quotes(evidenceIds: event.evidenceIds ?? [])
  }

  public func loadTimelineEvidence(workspaceId: String, event: TimelineEvent) async {
    await loadEvidence(workspaceId: workspaceId, evidenceIds: event.evidenceIds ?? [])
  }

  public func quotes(evidenceIds: [String]) -> [EvidenceItem] {
    evidenceIds.compactMap { evidence[$0] }
  }

  public func loadEvidence(workspaceId: String, evidenceIds: [String]) async {
    let generation = self.generation
    for id in evidenceIds {
      guard generation == self.generation else { return }
      guard evidence[id] == nil, evidenceRequested.insert(id).inserted else { continue }
      if let item = try? await api.evidenceItem(workspaceId: workspaceId, evidenceId: id) {
        guard generation == self.generation else { return }
        evidence[id] = item
      }
    }
  }

  /// The quotes of one driver, in the order of its evidence ids; unknown ids are skipped.
  public func quotes(for driver: DetailDriver) -> [EvidenceItem] {
    driver.evidenceIds.compactMap { evidence[$0] }
  }

  /// An evidence-based driver none of whose quotes could be loaded must not be shown as a claim:
  /// the screen shows a retry instead (evidence or nothing).
  public func evidenceMissing(for driver: DetailDriver) -> Bool {
    driver.basis == "evidence" && quotes(for: driver).isEmpty
  }

  public func reset() {
    generation = UUID()
    timelineCursor = nil
    canLoadMoreTimeline = false
    isLoadingTimeline = false
    timelineFailed = false
    evidenceRequested = []
    detail = nil
    evidence = [:]
    timeline = []
    message = nil
    phase = .idle
  }

  /// True when it was saved; the sheet stays open with the message otherwise.
  @discardableResult
  public func recordOutcome(workspaceId: String, clientId: String, event: OutcomeEvent, on date: Date) async
    -> Bool
  {
    do {
      try await api.recordOutcome(
        workspaceId: workspaceId, clientId: clientId, event: event, occurredAt: ReplayModel.day(date))
      await load(workspaceId: workspaceId, clientId: clientId)
      message = "Recorded."
      return true
    } catch {
      message = (error as? APIError)?.userMessage ?? "Could not record that."
      return false
    }
  }
}

/// Decision cards with feedback: what Firra suggests, and whether it was useful.
@MainActor @Observable
public final class DecisionsModel {
  public enum Phase: Equatable { case idle, loading, ready, failed(String) }
  public private(set) var phase: Phase = .idle
  public private(set) var cards: [DecisionItem] = []
  public private(set) var message: String?
  public private(set) var nextCursor: String?
  public private(set) var loadMoreFailed = false
  /// Quotes loaded on demand per card id; nil while not asked for.
  public private(set) var quotes: [String: [EvidenceItem]] = [:]
  public private(set) var quotesFailed: Set<String> = []

  private let api: APIClient
  public init(api: APIClient) { self.api = api }

  public func load(workspaceId: String) async {
    if cards.isEmpty { phase = .loading }
    do {
      let page = try await api.decisions(workspaceId: workspaceId)
      cards = page.items
      nextCursor = page.nextCursor
      loadMoreFailed = false
      phase = .ready
      await verify(workspaceId: workspaceId, cards: page.items)
    } catch {
      phase = cards.isEmpty ? .failed((error as? APIError)?.userMessage ?? "Could not load.") : .ready
      message = (error as? APIError)?.userMessage
    }
  }

  public struct CardSection: Identifiable {
    public let id: String
    public let cards: [DecisionItem]
  }

  /// Filtering preserves the server's order inside each nonempty importance group.
  public var sections: [CardSection] {
    [("Important", "important"), ("Worth attention", "attention"), ("FYI", "fyi"), ("This week", nil)]
      .compactMap { title, importance in
        let grouped = cards.filter { $0.importance == importance }
        return grouped.isEmpty ? nil : CardSection(id: title, cards: grouped)
      }
  }

  /// Cards without evidence ids contain metadata facts; other cards still require a loaded quote.
  public func isVerified(_ card: DecisionItem) -> Bool {
    card.evidenceIds.isEmpty || !(quotes[card.id] ?? []).isEmpty
  }

  private func verify(workspaceId: String, cards: [DecisionItem]) async {
    for card in cards where !card.evidenceIds.isEmpty && quotes[card.id] == nil { await loadEvidence(workspaceId: workspaceId, card: card) }
  }

  /// A client was restricted: forget everything loaded, it is fetched again under the new policy.
  public func reset() {
    cards = []
    quotes = [:]
    quotesFailed = []
    nextCursor = nil
    phase = .idle
  }

  public func loadMore(workspaceId: String) async {
    guard let cursor = nextCursor else { return }
    do {
      let page = try await api.decisions(workspaceId: workspaceId, cursor: cursor)
      let known = Set(cards.map(\.id))
      let fresh = page.items.filter { !known.contains($0.id) }
      cards += fresh
      nextCursor = page.nextCursor
      loadMoreFailed = false
      await verify(workspaceId: workspaceId, cards: fresh)
    } catch {
      loadMoreFailed = true
    }
  }

  /// Loads the quotes behind one card (each cited id, in order). A card never shows a claim without
  /// them: if none can be loaded the screen offers a retry.
  public func loadEvidence(workspaceId: String, card: DecisionItem) async {
    guard !card.evidenceIds.isEmpty else { return }
    quotesFailed.remove(card.id)
    var found: [EvidenceItem] = []
    for id in card.evidenceIds.prefix(3) {
      if let item = try? await api.evidenceItem(workspaceId: workspaceId, evidenceId: id) { found.append(item) }
    }
    if found.isEmpty { quotesFailed.insert(card.id) } else { quotes[card.id] = found }
  }

  public func rate(workspaceId: String, card: DecisionItem, verdict: FeedbackVerdict) async {
    do {
      try await api.sendFeedback(workspaceId: workspaceId, cardId: card.id, verdict: verdict, note: nil)
      message = nil
      await load(workspaceId: workspaceId)
    } catch {
      message = (error as? APIError)?.userMessage ?? "Could not save your answer."
    }
  }
}
