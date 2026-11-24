import Foundation
import Observation

public struct OnboardingSuggestions: Decodable, Sendable {
  public struct Item: Decodable, Sendable {
    public let domain: String
    public let threadCount: Int
    public let sampleNames: [String]
  }
  public let state: String
  public let items: [Item]
  public let scanned: Int?
}

public struct LocalAISettings: Codable, Sendable {
  public var executor: String
  public var backfillMonths: Int
  public var backgroundAcknowledged: Bool
  private enum CodingKeys: String, CodingKey { case executor, backfillMonths, backgroundAcknowledged }
  public init(executor: String, backfillMonths: Int, backgroundAcknowledged: Bool) {
    self.executor = executor; self.backfillMonths = backfillMonths
    self.backgroundAcknowledged = backgroundAcknowledged
  }
  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    executor = try values.decode(String.self, forKey: .executor)
    backfillMonths = try values.decode(Int.self, forKey: .backfillMonths)
    backgroundAcknowledged = try values.decodeIfPresent(Bool.self, forKey: .backgroundAcknowledged) ?? false
  }
}

public struct LocalAIStatus: Decodable, Sendable, Equatable {
  public struct Tool: Decodable, Sendable, Equatable {
    public let tool: String
    public let installed: Bool
    public let signedIn: Bool?
    public let version: String?
  }
  public struct Queue: Decodable, Sendable, Equatable {
    public let pending: Int
  }
  public let executor: String?
  public let tools: [Tool]
  public let queue: Queue
  public let state: String
}

@MainActor @Observable public final class WelcomeSetupModel {
  public private(set) var fileVault: FileVaultState = .unknown
  public var acknowledged = false
  public init() {}
  public func check(_ read: () async -> FileVaultState) async { fileVault = await read() }
  public var warning: String? {
    fileVault == .off ? "FileVault is off. Turn it on in System Settings → Privacy & Security → FileVault." : nil
  }
}

@MainActor @Observable public final class GoogleSetupModel {
  public var backfillMonths = 3
  public private(set) var connectionId: String?
  public private(set) var busy = false
  public private(set) var message: String?
  private let api: APIClient
  private let defaults: UserDefaults
  private let authorize: @MainActor () async throws -> GoogleSignInResult
  private let putSecret: @MainActor (String, String) async throws -> Void
  public init(api: APIClient, defaults: UserDefaults,
    authorize: @escaping @MainActor () async throws -> GoogleSignInResult,
    putSecret: @escaping @MainActor (String, String) async throws -> Void) {
    self.api = api; self.defaults = defaults; self.authorize = authorize; self.putSecret = putSecret
    connectionId = defaults.string(forKey: "setup.google.connection")
    let stored = defaults.integer(forKey: "setup.backfill")
    if [3, 6, 12].contains(stored) { backfillMonths = stored }
  }
  public func connect() async {
    guard !busy, [3, 6, 12].contains(backfillMonths) else { return }
    busy = true; defer { busy = false }
    do {
      let grant = try await authorize()
      // The engine looks the slot up by Python's lowercase UUID text; an uppercase id never matched ("missing_secret").
      let root = UUID().uuidString.lowercased()
      try await putSecret(grant.refreshToken, SecretSlot.googleRefreshToken(root))
      let gmail = try await api.connectGoogle(connectionId: root, accountLabel: grant.email,
        backfillMonths: backfillMonths)
      connectionId = gmail
      defaults.set(gmail, forKey: "setup.google.connection")
      defaults.set(backfillMonths, forKey: "setup.backfill")
      message = nil
    } catch { message = Self.text(error) }
  }
  public static func text(_ error: Error) -> String {
    if let config = error as? GoogleOAuthConfigError { return "Developer setup: " + config.userMessage }
    if let google = error as? GoogleSignInError {
      switch google {
      case .cancelled: return "Google connection was cancelled. Try again when you're ready."
      case .noRefreshToken: return "Google did not grant offline access. Connect again and allow the requested access."
      case .denied: return "Ask the Firra team to add your address as a Google test user."
      case .timeout: return "Google connection timed out. If access is blocked, ask the Firra team to add your address as a Google test user."
      case .missingScopes: return "Connect again and allow Gmail and Calendar read access."
      case .stateMismatch: return "Google connection could not be verified. Try again."
      case .exchangeFailed, .transport: return "Google could not be reached. Try again."
      }
    }
    return (error as? APIError)?.userMessage ?? "The Google connection could not be saved. Try again."
  }
}

@MainActor @Observable public final class ClientsSetupModel {
  public struct Choice: Identifiable {
    public var id: String { domain }
    public let domain: String
    public let threadCount: Int
    public let sampleNames: [String]
    public var selected = true
    public var name: String
    public var policy: ClientPolicy = .full
    public var confirmedExclusion = false
  }
  public private(set) var state = "none"
  /// Mail headers read so far while the engine ranks domains (shown as progress).
  public private(set) var scanned = 0
  public var choices: [Choice] = []
  /// The owner types each client; reading headers for suggestions is an optional extra.
  public var newName = ""
  public var newDomain = ""
  public private(set) var saved = false
  public private(set) var busy = false
  public private(set) var message: String?
  private var suggestionsMerged = false
  private let api: APIClient
  private let workspace: String
  private let defaults: UserDefaults
  public init(api: APIClient, workspace: String, defaults: UserDefaults) {
    self.api = api; self.workspace = workspace; self.defaults = defaults
  }
  /// "https://www.Acme.com/path", "@acme.com" and "acme.com" all become "acme.com"; anything else is nil.
  public static func normalizedDomain(_ text: String) -> String? {
    var value = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    if let range = value.range(of: "://") { value = String(value[range.upperBound...]) }
    value = String(value.split(separator: "/", maxSplits: 1).first ?? "")
    if let at = value.lastIndex(of: "@") { value = String(value[value.index(after: at)...]) }
    if value.hasPrefix("www.") { value.removeFirst(4) }
    let labels = value.split(separator: ".", omittingEmptySubsequences: false)
    let allowed = Set("abcdefghijklmnopqrstuvwxyz0123456789-")
    guard labels.count >= 2, labels.allSatisfy({
      !$0.isEmpty && !$0.hasPrefix("-") && !$0.hasSuffix("-") && $0.allSatisfy { allowed.contains($0) }
    }) else { return nil }
    return value
  }
  public var canSave: Bool { choices.contains { $0.selected } }
  public func addClient() {
    guard let domain = Self.normalizedDomain(newDomain) else {
      message = "Type the client's email domain, for example acme.com."; return
    }
    guard !choices.contains(where: { $0.domain == domain }) else {
      message = "\(domain) is already in the list."; return
    }
    let typed = newName.trimmingCharacters(in: .whitespacesAndNewlines)
    let name = typed.isEmpty ? (domain.split(separator: ".").first.map { String($0).capitalized } ?? domain) : typed
    choices.append(Choice(domain: domain, threadCount: 0, sampleNames: [], name: name))
    newName = ""; newDomain = ""; message = nil; saved = false
  }
  public func poll(connectionId: String, start: Bool = false) async {
    guard !busy else { return }
    busy = true; defer { busy = false }
    do {
      let result = try await api.onboardingSuggestions(connectionId: start ? connectionId : nil)
      state = result.state
      scanned = result.scanned ?? 0
      if state == "ready", !suggestionsMerged {
        suggestionsMerged = true
        let known = Set(choices.map(\.domain))
        choices += result.items.filter { !known.contains($0.domain) }.prefix(30).map {
          Choice(domain: $0.domain, threadCount: $0.threadCount, sampleNames: $0.sampleNames,
            selected: false,
            name: $0.domain.split(separator: ".").first.map { String($0).capitalized } ?? $0.domain)
        }
        saved = false
      }
      message = state == "failed" ? "Reading headers failed. Type your clients' domains instead, or try again." : nil
    } catch { state = "failed"; message = (error as? APIError)?.userMessage ?? "Client suggestions could not be loaded." }
  }
  public func reset() { state = "none"; choices = []; saved = false; message = nil; suggestionsMerged = false; newName = ""; newDomain = "" }
  public func saveClients() async {
    guard !busy else { return }
    guard choices.filter({ $0.selected }).allSatisfy({
      !$0.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        ($0.policy != .excluded || $0.confirmedExclusion)
    }) else { message = "Name each client and confirm every exclusion."; return }
    busy = true; defer { busy = false }
    do {
      for choice in choices where choice.selected {
        let key = "setup.created.\(workspace).\(choice.domain)"
        if let id = defaults.string(forKey: key) {
          try await api.renameClient(workspaceId: workspace, clientId: id, name: choice.name)
          try await api.setClientPolicy(workspaceId: workspace, clientId: id, policy: choice.policy)
        } else {
          let id = try await api.createClient(workspaceId: workspace, name: choice.name, policy: choice.policy,
            domains: [choice.domain])
          defaults.set(id, forKey: key)
        }
      }
      saved = true; message = "Reading mail for your clients."
    } catch { message = (error as? APIError)?.userMessage ?? "Clients could not be saved. Try again." }
  }
}

@MainActor @Observable public final class AISetupModel {
  public var background = false
  public var acknowledged = false
  public private(set) var busy = false
  public private(set) var status: LocalAIStatus?
  public private(set) var saved = false
  public private(set) var message: String?
  private let api: APIClient
  public init(api: APIClient) { self.api = api }
  public var waiting: String { "\(status?.queue.pending ?? 0) emails waiting" }
  public func load() async {
    do { status = try await api.aiStatus(); message = nil }
    catch { message = (error as? APIError)?.userMessage ?? "AI tools could not be checked. Try again." }
  }
  public private(set) var backfillMonths = 3
  public func loadSettings() async {
    do {
      let settings = try await api.aiSettings()
      background = settings.executor == "claude_code"
      acknowledged = settings.backgroundAcknowledged
      backfillMonths = settings.backfillMonths
      await load()
    } catch { message = "AI settings could not be loaded. Try again." }
  }
  public func reset() { background = false; acknowledged = false; saved = false; message = nil }
  public func save(backfillMonths: Int) async {
    guard !busy else { return }
    guard !background || acknowledged else {
      message = "Confirm that background processing uses your own AI subscription."; return
    }
    busy = true; defer { busy = false }
    do {
      try await api.aiSettings(LocalAISettings(executor: background ? "claude_code" : "conversation_only",
        backfillMonths: backfillMonths, backgroundAcknowledged: background && acknowledged))
      saved = true; message = nil
    } catch { message = (error as? APIError)?.userMessage ?? "AI settings could not be saved. Try again." }
  }
}

@MainActor @Observable public final class FirstResultsModel {
  public private(set) var finished = false
  public private(set) var message = "Waiting for mail sync and AI processing…"
  private let api: APIClient
  private let workspace: String
  private let clock: () -> Date
  private var started: Date?
  public init(api: APIClient, workspace: String, clock: @escaping () -> Date = Date.init) {
    self.api = api; self.workspace = workspace; self.clock = clock
  }
  public func begin() { started = clock(); finished = false }
  public func expire() { finished = true }
  public func poll() async {
    if started == nil { begin() }
    if clock().timeIntervalSince(started!) >= 60 { finished = true; return }
    guard !finished else { return }
    do {
      if !(try await api.attention(workspaceId: workspace)).items.isEmpty { finished = true; return }
      let status = try await api.localStatus()
      let ai = try await api.aiStatus()
      if finished { return }
      if status.connections.contains(where: { $0.state == "syncing" }) {
        message = "Firra is reading mail for your clients."
      } else if ai.state == "conversation_only" {
        message = "Ask your AI to process mail in conversation. Today is ready whenever you are."
      } else if ai.state == "needs_signin" || ai.state == "tool_missing" {
        message = "Sign in to Claude Code or install it to start background processing."
      } else if ai.state == "waiting_for_limit" {
        message = "Your AI subscription has reached its limit. Firra will resume when it resets."
      } else { message = "\(ai.queue.pending) emails waiting for AI processing." }
    } catch { message = "Waiting for the local engine. You can continue to Today." }
    if clock().timeIntervalSince(started!) >= 60 { finished = true }
  }
}

@MainActor @Observable public final class OnboardingModel {
  public enum Step: Int { case welcome, google, clients, ai, results, done }
  public private(set) var step: Step
  public let welcome = WelcomeSetupModel()
  public let google: GoogleSetupModel
  public let clients: ClientsSetupModel
  public let ai: AISetupModel
  public let sourceAccess: SourceAccessModel
  public let results: FirstResultsModel
  private let defaults: UserDefaults
  public init(api: APIClient, workspace: String, defaults: UserDefaults = .standard,
    sourceAccess: SourceAccessModel? = nil,
    authorize: @escaping @MainActor () async throws -> GoogleSignInResult,
    putSecret: @escaping @MainActor (String, String) async throws -> Void,
    clock: @escaping () -> Date = Date.init) {
    self.defaults = defaults
    step = Step(rawValue: defaults.integer(forKey: "setup.step")) ?? .welcome
    google = GoogleSetupModel(api: api, defaults: defaults, authorize: authorize, putSecret: putSecret)
    clients = ClientsSetupModel(api: api, workspace: workspace, defaults: defaults)
    ai = AISetupModel(api: api)
    self.sourceAccess = sourceAccess ?? SourceAccessModel(api: api)
    results = FirstResultsModel(api: api, workspace: workspace, clock: clock)
    if step != .welcome && step != .done && google.connectionId == nil { step = .google }
    if step == .results { results.begin() }
  }
  public func advance() {
    let allowed: Bool
    switch step {
    case .welcome: allowed = welcome.acknowledged
    case .google: allowed = google.connectionId != nil
    case .clients: allowed = clients.saved
    case .ai: allowed = ai.saved
    case .results: allowed = results.finished
    case .done: allowed = false
    }
    guard allowed, let next = Step(rawValue: step.rawValue + 1) else { return }
    step = next; defaults.set(step.rawValue, forKey: "setup.step")
    if step == .results { results.begin() }
  }
  public func finish() { results.expire(); if step == .results { advance() } }
  public func runAgain() {
    welcome.acknowledged = false; clients.reset(); ai.reset()
    step = .welcome; defaults.set(0, forKey: "setup.step")
  }
}
