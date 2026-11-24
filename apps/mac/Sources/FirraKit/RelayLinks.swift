import Foundation
import Observation

public struct RelaySettings: Decodable, Sendable {
  public enum State: String, Decodable, Sendable {
    case notRegistered = "not_registered", offline, connecting, online
    public var text: String {
      switch self {
      case .online: "Reachable from claude.ai"
      case .connecting: "Connecting to the relay…"
      case .offline: "Firra cannot reach the relay"
      case .notRegistered: "Firra is not registered with the relay yet"
      }
    }
  }
  public struct Link: Decodable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let scopes: [String]
    public let createdAt: String
    public var scopeText: String {
      scopes.contains("sources") ? "Client intelligence and client source excerpts" : "Client intelligence"
    }
  }
  public let relayUrl: String?
  public let state: State
  public let links: [Link]
}
public struct RelayLinkCreated: Decodable, Sendable {
  public let id: String
  public let name: String
  public let scopes: [String]
  public let url: String
}

@MainActor @Observable public final class RelayLinksModel {
  public static let linkExplanation = "Paste this as a custom connector URL in claude.ai (Settings, Connectors, Add custom connector) with no authentication. Anyone who has this link can ask Firra about your clients, so treat it like a password. It will not be shown again."
  public var address = ""
  public var name = "claude.ai"
  public var sources = false
  public private(set) var settings: RelaySettings?
  public private(set) var busy = false
  public private(set) var error: String?
  public private(set) var createdURL: String?
  public private(set) var copyMessage: String?
  public private(set) var revoking: RelaySettings.Link?
  private let api: APIClient
  private let copy: (String) -> Bool
  private var generation = 0
  public init(api: APIClient, copy: @escaping (String) -> Bool) { self.api = api; self.copy = copy }

  private func refresh() async throws { settings = try await api.relaySettings() }
  public func load() async {
    guard !busy else { return }; busy = true; defer { busy = false }
    do { try await refresh(); error = nil }
    catch { self.error = "Relay status could not be loaded. Try again." }
  }
  public func saveAddress() async {
    guard !busy else { return }; busy = true; error = nil; defer { busy = false }
    do { settings = try await api.relaySettings(address: address.trimmingCharacters(in: .whitespacesAndNewlines)); address = "" }
    catch { self.error = Self.text(error, invalid: "Enter the https relay address Firra gave you, without a path.") }
  }
  public func create() async {
    guard !busy, createdURL == nil else { return }
    guard settings?.relayUrl != nil else { error = "Set the relay address first."; return }
    let label = name.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !label.isEmpty, label.count <= 60 else { error = "Name the link using 1 to 60 characters."; return }
    busy = true; error = nil; defer { busy = false }
    let requestGeneration = generation
    do {
      let result = try await api.createRelayLink(name: label, sources: sources)
      if requestGeneration == generation { createdURL = result.url; copyMessage = nil; sources = false }
      do { try await refresh() }
      catch { self.error = "Link created, but the list could not be refreshed. Try again." }
    } catch { self.error = Self.text(error, invalid: "Check the link name and permissions, then try again.") }
  }
  public func copyCreatedLink() {
    guard let createdURL else { return }
    copyMessage = copy(createdURL) ? "Copied. Treat this link like a password." : "Could not copy. Try again."
  }
  public func dismissCreatedLink() { generation += 1; createdURL = nil; copyMessage = nil }
  public func requestRevoke(_ link: RelaySettings.Link) { if !busy { revoking = link } }
  public func cancelRevoke() { revoking = nil }
  public func confirmRevoke() async {
    guard !busy, let link = revoking else { return }
    revoking = nil; busy = true; error = nil; defer { busy = false }
    do {
      try await api.revokeRelayLink(link.id)
      do { try await refresh() }
      catch { self.error = "Link revoked, but the list could not be refreshed. Try again." }
    } catch { self.error = Self.text(error, invalid: "The link could not be revoked. Try again.") }
  }
  private static func text(_ error: Error, invalid: String) -> String {
    if case let APIError.server(status, _, _, _) = error {
      if status == 409 { return "Set the relay address and wait for Firra to connect, then try again." }
      if status == 422 { return invalid }
    }
    return "Firra could not complete the request. Try again."
  }
}
