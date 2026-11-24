import Foundation
import Observation

public enum SlackManifest {
  public static var json: String {
    // Bundled from the engine's manifest(), with a captured fixture checked in tests.
    let resources = Bundle.main.resourceURL.flatMap { Bundle(url: $0.appending(path: "Firra_FirraKit.bundle")) }
      ?? Bundle.module
    let url = resources.url(forResource: "slack-manifest", withExtension: "json")!
    return String(decoding: try! Data(contentsOf: url), as: UTF8.self)
  }
  public static var creationURL: URL {
    var parts = URLComponents(string: "https://api.slack.com/apps")!
    parts.queryItems = [URLQueryItem(name: "new_app", value: "1"),
      URLQueryItem(name: "manifest_json", value: json)]
    return parts.url!
  }
}

public struct SlackChannel: Decodable, Sendable, Equatable {
  public let channelId: String
  public let label: String
  public let kind: String
  public let suggestedClientId: String?
  public let reason: String
  public var direct: Bool { kind == "direct" || kind == "group_direct" }
  // The route has no is_ext_shared flag. Require consent unless metadata proves an external match.
  public var needsColleagueConsent: Bool { kind != "direct" && reason != "slack_connect" }
  public var reasonText: String {
    switch reason {
    case "channel_name": "Channel name matches a client"
    case "member_domain": "Member domain matches a client"
    case "slack_connect": "Slack Connect channel"
    default: "Choose a client or skip"
    }
  }
}

public struct SlackChannelChoice: Identifiable, Sendable, Equatable {
  public let channel: SlackChannel
  public var clientId: String?
  public var colleagueConsent = false
  public var id: String { channel.channelId }
  public var included: Bool { clientId != nil && (!channel.needsColleagueConsent || colleagueConsent) }
}

@MainActor @Observable
public final class SlackConnectModel {
  public private(set) var busy = false
  public private(set) var mappingConnection: String?
  public private(set) var connections: [LocalStatus.Connection] = []
  public private(set) var clients: [SetupClient] = []
  public var choices: [SlackChannelChoice] = []
  public private(set) var message: String?
  public var disconnecting: String?
  private var skippedDirects: [SlackChannelChoice] = []
  private var choicesConnection: String?
  private let api: APIClient
  private let workspace: String
  private let putSecret: @MainActor (String, String) async throws -> Void

  public init(api: APIClient, workspace: String,
    putSecret: @escaping @MainActor (String, String) async throws -> Void) {
    self.api = api; self.workspace = workspace; self.putSecret = putSecret
  }

  public func load() async {
    guard !busy else { return }; busy = true; defer { busy = false }
    do { try await refresh() } catch { message = Self.text(error) }
  }
  private func refresh() async throws {
    connections = try await api.localStatus().connections.filter { $0.provider == "slack" && $0.state != "revoked" }
    clients = try await api.setupClients(workspaceId: workspace).items.filter { $0.policy != .excluded }
  }

  public func connect(token: String, accountLabel: String) async {
    guard !busy else { return }
    guard token.hasPrefix("xoxp-") else { message = "Paste the User OAuth Token starting with xoxp-."; return }
    guard !accountLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      message = "Enter a name for this Slack workspace."; return
    }
    busy = true; message = nil; defer { busy = false }
    let id = UUID().uuidString.lowercased()
    do {
      // Only this transient argument crosses the control channel; HTTP receives no token.
      try await putSecret(token, SecretSlot.slackUserToken(id))
      try await api.connectSlack(connectionId: id, accountLabel: accountLabel)
      mappingConnection = id; choices = []; skippedDirects = []; choicesConnection = nil
      try await refresh()
      try await fetchChoices(id)
    } catch { message = Self.text(error) }
  }

  private func fetchChoices(_ id: String) async throws {
    let previous = choicesConnection == id ? Dictionary(uniqueKeysWithValues: choices.map { ($0.id, $0) }) : [:]
    let allowed = Set(clients.map(\.id))
    let channels = try await api.slackChannels(id)
    var hidden: [SlackChannelChoice] = []
    choices = channels.compactMap { channel in
      let suggested = channel.suggestedClientId.flatMap { allowed.contains($0) ? $0 : nil }
      // Unmatched DMs have no client context and are never offered for ingestion.
      guard !channel.direct || suggested != nil || previous[channel.channelId]?.clientId != nil else {
        hidden.append(SlackChannelChoice(channel: channel, clientId: nil))
        return nil
      }
      if let old = previous[channel.channelId] {
        return SlackChannelChoice(channel: channel, clientId: old.clientId.flatMap { allowed.contains($0) ? $0 : nil },
          colleagueConsent: old.colleagueConsent)
      }
      return SlackChannelChoice(channel: channel, clientId: suggested)
    }
    skippedDirects = hidden
    mappingConnection = id; choicesConnection = id
  }
  public func remap(_ id: String) async {
    guard !busy else { return }; busy = true; message = nil; defer { busy = false }
    do { try await refresh(); try await fetchChoices(id) } catch { message = Self.text(error) }
  }
  public func save() async {
    guard !busy, let id = mappingConnection else { return }
    if choices.contains(where: { $0.clientId != nil && !$0.included }) {
      message = "Allow reading colleagues’ messages for selected internal channels, or choose Skip."; return
    }
    busy = true; message = nil; defer { busy = false }
    do { try await api.saveSlackScope(id, channels: choices + skippedDirects); mappingConnection = nil; try await refresh() }
    catch { message = Self.text(error) }
  }
  public func sync(_ id: String) async {
    guard !busy else { return }; busy = true; message = nil; defer { busy = false }
    do { try await api.syncConnection(id); try await refresh() } catch { message = Self.text(error) }
  }
  public func disconnect() async {
    guard !busy, let id = disconnecting else { return }
    disconnecting = nil; busy = true; message = nil; defer { busy = false }
    do {
      // Engine's secret_delete event removes the Keychain slot through EngineSupervisor.
      try await api.disconnectSlack(id)
      if choicesConnection == id { mappingConnection = nil; choices = []; skippedDirects = []; choicesConnection = nil }
      try await refresh()
    } catch { message = Self.text(error) }
  }
  public func cancelMapping() { mappingConnection = nil }

  public static func text(_ error: Error) -> String {
    if case let APIError.server(_, code, _, _) = error {
      switch code {
      case "not_a_user_token": return "Paste the User OAuth Token starting with xoxp-."
      case "token_invalid": return "This token is expired or invalid. Install the app again and copy a new token."
      case "missing_scopes": return "Required read permissions are missing. Create the app from the Firra manifest."
      case "scopes_too_broad": return "This token has extra permissions. Use only the read permissions in the Firra manifest."
      case "account_already_connected": return "This Slack workspace is already connected."
      case "missing_secret": return "The engine did not receive the token. Paste it and try again."
      case "connection_conflict": return "This connection could not be created. Try again."
      case "connection_unavailable": return "Reconnect this Slack workspace."
      case "slack_unreachable", "slack_unavailable": return "Slack is unavailable. Try again shortly."
      default: return "Slack could not complete the request. Try again."
      }
    }
    return "Slack could not complete the request. Try again."
  }
}
