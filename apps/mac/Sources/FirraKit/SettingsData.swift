import Foundation
import Observation

public struct BackupCreateResponse: Decodable, Sendable {
  public let recoveryPhrase: String
  public let filename: String
  public let backupB64: String
}
public struct BackupRestoreResponse: Decodable, Sendable {
  public let clients: Int
  public let pendingChannelMappings: Int
  public let pendingFeedback: Int
}
public struct DeleteDataResponse: Decodable, Sendable {
  public let deleted: [String: Int]
  public let secrets: Int
  public let revoked: Int
  public let revokeFailed: Int
}
/// Re-encoding this allowlist excludes any unexpected fields from a diagnostics export.
public struct Diagnostics: Codable, Sendable {
  public struct Client: Codable, Sendable { public let status: String; public let policy: String; public let count: Int }
  public struct Record: Codable, Sendable {
    public let provider: String; public let kind: String; public let processingState: String; public let count: Int
  }
  public struct Job: Codable, Sendable {
    public let type: String; public let status: String; public let errorClass: String?; public let count: Int
    private enum CodingKeys: String, CodingKey { case type, status, errorClass, count }
    public func encode(to encoder: Encoder) throws {
      var values = encoder.container(keyedBy: CodingKeys.self)
      try values.encode(type, forKey: .type); try values.encode(status, forKey: .status)
      try values.encode(errorClass, forKey: .errorClass); try values.encode(count, forKey: .count)
    }
  }
  public struct Connection: Codable, Sendable {
    public let provider: String; public let status: String; public let lastErrorClass: String?; public let lastSyncedAt: String?
    private enum CodingKeys: String, CodingKey { case provider, status, lastErrorClass, lastSyncedAt }
    public func encode(to encoder: Encoder) throws {
      var values = encoder.container(keyedBy: CodingKeys.self)
      try values.encode(provider, forKey: .provider); try values.encode(status, forKey: .status)
      try values.encode(lastErrorClass, forKey: .lastErrorClass); try values.encode(lastSyncedAt, forKey: .lastSyncedAt)
    }
  }
  public let generatedAt: String
  public let engineVersion: String
  public let platform: String
  public let migrations: Int
  public let clients: [Client]
  public let records: [Record]
  public let jobs: [Job]
  public let connections: [Connection]
  public let dataBytes: Int
}

public struct RetentionSettings: Decodable, Sendable {
  public let bodyMonths: Int?
  private enum CodingKeys: String, CodingKey { case bodyMonths }
  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    // Null is unlimited; a missing key must not silently become unlimited.
    bodyMonths = try values.decode(Int?.self, forKey: .bodyMonths)
    if let bodyMonths, !(1...120).contains(bodyMonths) {
      throw DecodingError.dataCorruptedError(forKey: .bodyMonths, in: values,
        debugDescription: "Retention months out of range")
    }
  }
}

@MainActor public struct SettingsDataActions {
  public var savePanel: (String) async -> URL?
  public var openPanel: () async -> URL?
  public var read: (URL) throws -> Data
  public var write: (Data, URL) throws -> Void
  public var copy: (String) -> Bool
  public var prepareDeletion: () -> Void
  public var deleteKeychain: () throws -> Void
  public var stopEngine: () async throws -> Void
  public var deleteFiles: () async throws -> Void
  public var resetDefaults: () throws -> Void
  public var quit: () -> Void
  public init(savePanel: @escaping (String) async -> URL?, openPanel: @escaping () async -> URL?,
    read: @escaping (URL) throws -> Data, write: @escaping (Data, URL) throws -> Void,
    copy: @escaping (String) -> Bool, prepareDeletion: @escaping () -> Void,
    deleteKeychain: @escaping () throws -> Void, stopEngine: @escaping () async throws -> Void,
    deleteFiles: @escaping () async throws -> Void, resetDefaults: @escaping () throws -> Void,
    quit: @escaping () -> Void) {
    self.savePanel = savePanel; self.openPanel = openPanel; self.read = read; self.write = write
    self.copy = copy; self.prepareDeletion = prepareDeletion; self.deleteKeychain = deleteKeychain
    self.stopEngine = stopEngine; self.deleteFiles = deleteFiles; self.resetDefaults = resetDefaults; self.quit = quit
  }
}

@MainActor @Observable public final class SettingsDataModel {
  public enum DeleteStep: Int { case engine, keychain, stop, files, defaults, complete }
  public private(set) var busy = false
  public private(set) var recoveryPhrase: String?
  public var savedPhrase = false
  public private(set) var backupAvailable = false
  public private(set) var restoreFile: URL?
  public var restorePhrase = ""
  public private(set) var message: String?
  public var confirmation = ""
  public private(set) var deletionStarted = false
  public private(set) var deleteStep: DeleteStep = .engine
  public private(set) var deleteError: String?
  public static let retentionExplanation = "Older text is deleted from this Mac. Dates, who wrote to whom, short verified quotes and everything Firra learned from the text stay. Shortening applies immediately and cannot be undone (Firra can re-read recent history from Gmail or Slack, not older)."
  public private(set) var bodyMonths: Int? = 12
  public private(set) var retentionLoaded = false
  public private(set) var retentionLoading = false
  public private(set) var retentionSaving = false
  public private(set) var retentionError: String?
  public private(set) var pendingRetentionMonths: Int?
  private let api: APIClient
  private let actions: SettingsDataActions
  private var sensitiveGeneration = 0
  private var backupData: Data?
  private var backupFilename = ""
  public init(api: APIClient, actions: SettingsDataActions) { self.api = api; self.actions = actions }
  public func loadRetention() async {
    guard !busy, pendingRetentionMonths == nil, !deletionStarted else { return }
    busy = true; retentionLoading = true
    defer { busy = false; retentionLoading = false }
    do { bodyMonths = try await api.retentionSettings().bodyMonths; retentionLoaded = true; retentionError = nil }
    catch { retentionError = "Retention could not be loaded. Try again." }
  }
  public func requestRetention(_ months: Int?) async {
    guard retentionLoaded, !busy, !deletionStarted, pendingRetentionMonths == nil, months != bodyMonths else { return }
    guard months == nil || [3, 6, 12, 24].contains(months!) else { return }
    retentionError = nil
    if let months, bodyMonths == nil || months < bodyMonths! {
      pendingRetentionMonths = months
    } else { await saveRetention(months) }
  }
  public func cancelRetention() { pendingRetentionMonths = nil }
  public func confirmRetention() async {
    guard !busy, !deletionStarted, let months = pendingRetentionMonths else { return }
    pendingRetentionMonths = nil
    await saveRetention(months)
  }
  private func saveRetention(_ months: Int?) async {
    let previous = bodyMonths
    bodyMonths = months; busy = true; retentionSaving = true
    defer { busy = false; retentionSaving = false }
    do { bodyMonths = try await api.retentionSettings(bodyMonths: months).bodyMonths; retentionError = nil }
    catch { bodyMonths = previous; retentionError = "Retention could not be saved. Try again." }
  }
  public func createBackup() async {
    guard !busy, !backupAvailable, !deletionStarted else { return }
    busy = true; defer { busy = false }
    let generation = sensitiveGeneration
    do {
      let response = try await api.createBackup()
      guard generation == sensitiveGeneration else { return }
      guard let data = Data(base64Encoded: response.backupB64) else { throw APIError.transport("invalid backup") }
      backupData = data; backupFilename = response.filename
      recoveryPhrase = response.recoveryPhrase; savedPhrase = false; backupAvailable = true; message = nil
    } catch { message = "Backup could not be created. Try again." }
  }
  public func copyPhrase() {
    guard let recoveryPhrase else { return }
    message = actions.copy(recoveryPhrase) ? "Phrase copied. Write it down somewhere safe." : "Could not copy the phrase."
  }
  public func dismissPhrase() { recoveryPhrase = nil }
  public func discardBackup() {
    sensitiveGeneration += 1
    recoveryPhrase = nil; backupData = nil; backupAvailable = false; savedPhrase = false
  }
  public func saveBackup() async {
    guard !busy, savedPhrase, let data = backupData else { return }
    // Once acknowledged, neither a cancelled panel nor a write retry can show the phrase again.
    recoveryPhrase = nil
    busy = true; defer { busy = false }
    guard let url = await actions.savePanel(backupFilename) else { message = "Backup was not saved. Choose Save backup to try again."; return }
    do { try actions.write(data, url); discardBackup(); message = "Backup saved." }
    catch { message = "Backup could not be saved. Your encrypted backup is still ready to save." }
  }
  public func chooseRestore() async {
    guard !busy, !deletionStarted else { return }
    restoreFile = await actions.openPanel(); restorePhrase = ""; message = nil
    if let restoreFile, restoreFile.pathExtension.lowercased() != "firrabackup" {
      self.restoreFile = nil; message = "Choose a .firrabackup file."
    }
  }
  public func restore() async {
    guard !busy, let restoreFile, !restorePhrase.isEmpty else { return }
    busy = true; defer { busy = false; restorePhrase = "" }
    do {
      let result = try await api.restoreBackup(actions.read(restoreFile), phrase: restorePhrase)
      self.restoreFile = nil
      message = "Restored \(result.clients) clients, \(result.pendingChannelMappings) mappings and \(result.pendingFeedback) feedback items; mappings and feedback apply after the first sync."
    } catch APIError.server(422, "invalid_backup", _, _) { message = "Wrong recovery phrase or the file was changed" }
    catch APIError.server(409, "workspace_not_empty", _, _) { message = "Restore works on a fresh install only" }
    catch { message = "Backup could not be restored. Try again." }
  }
  public func cancelRestore() { restoreFile = nil; restorePhrase = "" }
  public func exportDiagnostics() async {
    guard !busy, !deletionStarted else { return }
    busy = true; defer { busy = false }
    do {
      let diagnostics = try await api.exportDiagnostics()
      let data = try JSONEncoder.api.encode(diagnostics)
      guard let url = await actions.savePanel("firra-diagnostics.json") else { message = "Diagnostics were not saved."; return }
      try actions.write(data, url); message = "Diagnostics saved."
    } catch { message = "Diagnostics could not be exported. Try again." }
  }
  static let metricsKeys: Set<String> = [
    "generated_at", "engine_version", "as_of", "usefulness", "precision", "lead_time", "important_per_week",
  ]
  public func exportMetrics() async {
    guard !busy, !deletionStarted else { return }
    busy = true; defer { busy = false }
    do {
      let all = try await api.exportMetrics()
      let kept = all.filter { Self.metricsKeys.contains($0.key) }
      let data = try JSONSerialization.data(withJSONObject: kept, options: [.prettyPrinted, .sortedKeys])
      guard let url = await actions.savePanel("firra-metrics.json") else { message = "Metrics were not saved."; return }
      try actions.write(data, url); message = "Metrics saved. They contain no names, messages or addresses; send the file to us."
    } catch { message = "Metrics could not be exported. Try again." }
  }
  public var remaining: String {
    switch deleteStep {
    case .engine: return "The engine's data deletion is unconfirmed; local Keychain and file cleanup has not finished."
    case .keychain: return "Keychain items may remain; local files and app settings remain."
    case .stop: return "The engine has not stopped; local files and app settings remain."
    case .files: return "Some local files may remain; app settings remain."
    case .defaults: return "App settings remain."
    case .complete: return "Deletion completed."
    }
  }
  public func deleteEverything() async {
    guard !busy, confirmation == "delete everything" else {
      if !busy { deleteError = "Type delete everything exactly to confirm." }; return
    }
    busy = true; deleteError = nil; defer { busy = false }
    if !deletionStarted { deletionStarted = true; cancelRetention(); discardBackup(); cancelRestore(); actions.prepareDeletion() }
    do {
      if deleteStep == .engine { _ = try await api.deleteEverything(); deleteStep = .keychain }
      if deleteStep == .keychain { try actions.deleteKeychain(); deleteStep = .stop }
      if deleteStep == .stop { try await actions.stopEngine(); deleteStep = .files }
      if deleteStep == .files { try await actions.deleteFiles(); deleteStep = .defaults }
      if deleteStep == .defaults { try actions.resetDefaults(); deleteStep = .complete }
      if deleteStep == .complete { actions.quit() }
    } catch { deleteError = "Deletion did not finish. " + remaining + " Retry to continue." }
  }
}
