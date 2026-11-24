import Foundation
import Observation

/// The token-free install instructions returned by GET /local/mcp/connect.
public struct McpConnect: Decodable, Sendable {
  public struct Item: Decodable, Sendable, Identifiable {
    public enum Kind: String, Decodable, Sendable { case command, deeplink, config }
    public let id: String
    public let title: String
    public let kind: Kind
    public let value: String
    public let note: String?
  }
  public let serverName: String
  public let items: [Item]
}

@MainActor @Observable public final class ConnectAIToolsModel {
  public private(set) var items: [McpConnect.Item] = []
  public private(set) var loading = false
  public private(set) var testing = false
  public private(set) var error: String?
  public private(set) var actionMessage: String?
  public private(set) var connectionMessage: String?
  public let sourceAccess: SourceAccessModel
  public let processingTools: SourceAccessModel
  public let relay: RelayLinksModel
  private let api: APIClient
  private let copy: (String) -> Bool
  private let open: (URL) -> Bool
  public init(api: APIClient, sourceAccess: SourceAccessModel? = nil, processingTools: SourceAccessModel? = nil,
    copy: @escaping (String) -> Bool, open: @escaping (URL) -> Bool) {
    self.api = api; self.copy = copy; self.open = open
    relay = RelayLinksModel(api: api, copy: copy)
    self.sourceAccess = sourceAccess ?? SourceAccessModel(api: api)
    self.processingTools = processingTools ?? SourceAccessModel(api: api, setting: .processingTools)
  }
  public func load() async {
    guard !loading else { return }
    loading = true; defer { loading = false }
    do { items = try await api.mcpConnect().items; error = nil }
    catch { items = []; self.error = "Connection instructions could not be loaded. Try again." }
  }
  public func act(on item: McpConnect.Item) {
    switch item.kind {
    case .command, .config:
      actionMessage = copy(item.value) ? "Copied." : "Could not copy. Try again."
    case .deeplink:
      // Only the two native install protocols belong to this screen.
      let expected = item.id == "cursor" ? "cursor" : item.id == "vscode" ? "vscode" : ""
      guard let url = URL(string: item.value), !expected.isEmpty, url.scheme == expected else {
        actionMessage = "This install link is unavailable."; return
      }
      actionMessage = open(url) ? "Opened \(item.title)." : "Could not open \(item.title). Check that it is installed."
    }
  }
  public func testConnection() async {
    guard !testing else { return }
    testing = true; defer { testing = false }
    do { _ = try await api.localStatus(); connectionMessage = "The local engine is running." }
    catch { connectionMessage = "The local engine could not be reached. Try again." }
  }
}
