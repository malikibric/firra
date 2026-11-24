import Foundation
import Observation

public struct McpSettings: Decodable, Sendable, Equatable {
  public let sourceAccess: Bool
  public let processingTools: Bool

  private enum CodingKeys: String, CodingKey { case sourceAccess, processingTools }
  public init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    sourceAccess = try container.decode(Bool.self, forKey: .sourceAccess)
    // Older source-access responses omit this opt-in flag; present values must still be booleans.
    processingTools = try container.contains(.processingTools)
      ? container.decode(Bool.self, forKey: .processingTools) : false
  }
}

/// An engine-backed opt-in switch. Source access is shared with onboarding; processing stays in Connect AI tools.
@MainActor @Observable public final class SourceAccessModel {
  public enum Setting { case sourceAccess, processingTools }
  public static let title = "Let my AI read excerpts of client mail, Slack and meetings"
  public static let explanation = "Off by default. When on, the AI tool you connect can ask Firra for short excerpts about one client at a time. That text goes to the AI provider you use (Claude, ChatGPT ...). Firra never gives an AI your whole inbox or all of Slack, only the clients you added. Switch it off any time."
  public var title: String {
    setting == .sourceAccess ? Self.title : "Let my AI process new mail in the conversation"
  }
  public var explanation: String {
    setting == .sourceAccess ? Self.explanation : "Off by default. Firra already reads new mail in the background with your own Claude Code. When on, your AI tool can also fetch batches of mail from Firra and send back what it found. Each batch uses a lot of your AI plan (about 15,000 tokens), so leave this off unless you have no background AI. Takes effect after you restart Firra and your AI tool."
  }
  public var confirmationLabel: String { setting == .sourceAccess ? "Allow excerpts" : "Allow processing" }
  public var settingName: String { setting == .sourceAccess ? "Source access" : "Processing tools" }
  public private(set) var enabled = false
  public private(set) var loaded = false
  public private(set) var loading = false
  public private(set) var saving = false
  public private(set) var confirmationRequired = false
  public private(set) var message: String?
  private let api: APIClient
  private let setting: Setting
  public init(api: APIClient, setting: Setting = .sourceAccess) { self.api = api; self.setting = setting }

  private func value(in settings: McpSettings) -> Bool {
    setting == .sourceAccess ? settings.sourceAccess : settings.processingTools
  }

  public func load() async {
    guard !loading, !saving, !confirmationRequired else { return }
    loading = true; defer { loading = false }
    do { enabled = value(in: try await api.mcpSettings()); loaded = true; message = nil }
    catch { message = "\(settingName) could not be loaded. Try again." }
  }

  public func requestChange(to value: Bool) async {
    guard loaded, !loading, !saving, !confirmationRequired, value != enabled else { return }
    message = nil
    if value { confirmationRequired = true }
    else { await save(false) }
  }
  public func cancelConfirmation() { confirmationRequired = false }
  public func confirm() async {
    guard confirmationRequired, !loading, !saving else { return }
    confirmationRequired = false
    await save(true)
  }
  private func save(_ value: Bool) async {
    let previous = enabled
    enabled = value; saving = true; defer { saving = false }
    do {
      let settings: McpSettings
      switch setting {
      case .sourceAccess: settings = try await api.mcpSettings(sourceAccess: value)
      case .processingTools: settings = try await api.mcpSettings(processingTools: value)
      }
      enabled = self.value(in: settings); message = nil
    }
    catch { enabled = previous; message = "\(settingName) could not be saved. Try again." }
  }
}
