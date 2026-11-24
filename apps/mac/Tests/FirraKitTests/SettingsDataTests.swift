import CoreFoundation
import Foundation
import Security
import Testing
@testable import FirraKit

@MainActor private final class FakeSettingsIO {
  var saveURL: URL? = URL(fileURLWithPath: "/synthetic/backup.firrabackup")
  var openURL: URL? = URL(fileURLWithPath: "/synthetic/restore.firrabackup")
  var input = Data("encrypted-fixture".utf8)
  var saved: [(Data, URL)] = []
  var filenames: [String] = []
  var copied: [String] = []
  var events: [String] = []
  var fail = ""
  func step(_ name: String) throws { events.append(name); if fail == name { throw CocoaError(.fileWriteUnknown) } }
  var actions: SettingsDataActions {
    SettingsDataActions(savePanel: { self.filenames.append($0); return self.saveURL },
      openPanel: { self.openURL }, read: { _ in try self.step("read"); return self.input },
      write: { data, url in try self.step("write"); self.saved.append((data, url)) },
      copy: { self.copied.append($0); return true }, prepareDeletion: { self.events.append("pause") },
      deleteKeychain: { try self.step("keychain") }, stopEngine: { try self.step("stop") },
      deleteFiles: { try self.step("files") }, resetDefaults: { try self.step("defaults") },
      quit: { self.events.append("quit") })
  }
}

@MainActor @Suite struct SettingsDataTests {
  let backup = #"{"recovery_phrase":"synthetic phrase shown once","filename":"firra-backup-20261005.firrabackup","backup_b64":"ZW5jcnlwdGVkLWZpeHR1cmU="}"#
  let restored = #"{"clients":2,"pending_channel_mappings":3,"pending_feedback":4}"#
  let deleted = #"{"deleted":{"client":2},"secrets":3,"revoked":1,"revoke_failed":0}"#
  let diagnostics = #"{"generated_at":"2026-10-05T10:00:00Z","engine_version":"test","platform":"test","migrations":32,"clients":[],"records":[],"jobs":[{"type":"sync.incremental","status":"succeeded","error_class":null,"count":1}],"connections":[{"provider":"gmail","status":"active","last_error_class":null,"last_synced_at":null}],"data_bytes":512,"unexpected_private_text":"must not be exported"}"#
  func api(_ stub: StubTransport) -> APIClient { APIClient(base: testBaseURL, transport: stub, tokenProvider: { "synthetic" }) }

  @Test func phraseIsShownOnceAndSavingRequiresAcknowledgement() async {
    let io = FakeSettingsIO()
    let stub = StubTransport([.init(status: 200, body: backup)])
    let model = SettingsDataModel(api: api(stub), actions: io.actions)
    await model.createBackup()
    #expect(model.recoveryPhrase == "synthetic phrase shown once")
    model.copyPhrase(); #expect(io.copied == ["synthetic phrase shown once"])
    await model.saveBackup(); #expect(io.saved.isEmpty && io.filenames.isEmpty)
    model.savedPhrase = true; io.saveURL = nil
    await model.saveBackup()
    #expect(model.recoveryPhrase == nil && model.backupAvailable && io.saved.isEmpty)
    await model.createBackup(); #expect(stub.requests.count == 1 && model.recoveryPhrase == nil)
    io.saveURL = URL(fileURLWithPath: "/synthetic/backup.firrabackup")
    io.fail = "write"; await model.saveBackup()
    #expect(model.backupAvailable && model.message?.contains("still ready") == true)
    io.fail = ""; await model.saveBackup()
    #expect(!model.backupAvailable && model.recoveryPhrase == nil)
    #expect(io.saved[0].0 == Data("encrypted-fixture".utf8))
    #expect(io.filenames.allSatisfy { $0 == "firra-backup-20261005.firrabackup" })
    #expect(!String(decoding: io.saved[0].0, as: UTF8.self).contains("phrase"))
    let relaunched = SettingsDataModel(api: api(stub), actions: io.actions)
    #expect(relaunched.recoveryPhrase == nil && !relaunched.backupAvailable)
  }

  @Test func leavingSettingsDiscardsEvenALateBackupResponse() async {
    let io = FakeSettingsIO()
    let gate = GatedTransport(reply: .init(status: 200, body: backup))
    let model = SettingsDataModel(api: APIClient(base: testBaseURL, transport: gate,
      tokenProvider: { "synthetic" }), actions: io.actions)
    let create = Task { await model.createBackup() }
    await gate.waitForRequests(1)
    model.discardBackup()
    gate.releaseAll(); await create.value
    #expect(model.recoveryPhrase == nil && !model.backupAvailable && !model.busy)
  }

  @Test func cancelledPhraseAndBackupFailuresLeaveNoReusablePhrase() async {
    let io = FakeSettingsIO()
    let stub = StubTransport([.init(status: 500, body: "{}"), .init(status: 200, body: backup)])
    let model = SettingsDataModel(api: api(stub), actions: io.actions)
    await model.createBackup(); #expect(model.recoveryPhrase == nil && model.message != nil)
    await model.createBackup(); model.discardBackup()
    #expect(model.recoveryPhrase == nil && !model.backupAvailable && io.saved.isEmpty)
  }

  @Test func restoreMapsErrorsClearsThePhraseAndShowsCounts() async throws {
    let io = FakeSettingsIO()
    let stub = StubTransport([
      .init(status: 422, body: #"{"error":"invalid_backup","message":"private details","request_id":"test"}"#),
      .init(status: 409, body: #"{"error":"workspace_not_empty","message":"private details","request_id":"test"}"#),
      .init(status: 200, body: restored)])
    let model = SettingsDataModel(api: api(stub), actions: io.actions)
    await model.chooseRestore()
    model.restorePhrase = "wrong"; await model.restore()
    #expect(model.message == "Wrong recovery phrase or the file was changed" && model.restorePhrase.isEmpty)
    model.restorePhrase = "valid"; await model.restore()
    #expect(model.message == "Restore works on a fresh install only" && model.restorePhrase.isEmpty)
    model.restorePhrase = "valid"; await model.restore()
    #expect(model.message?.contains("2 clients, 3 mappings and 4 feedback") == true)
    #expect(model.message?.contains("after the first sync") == true && model.restoreFile == nil)
    let body = try #require(JSONSerialization.jsonObject(with: stub.requests[0].httpBody!) as? [String: String])
    #expect(body["phrase"] == "wrong" && body["backup_b64"] == io.input.base64EncodedString())
    #expect(stub.requests.allSatisfy { $0.url?.path == "/local/backup/restore" })
  }

  @Test func restorePanelCancellationAndFileErrorsDoNotSendData() async {
    let io = FakeSettingsIO(); io.openURL = nil
    let stub = StubTransport([])
    let model = SettingsDataModel(api: api(stub), actions: io.actions)
    await model.chooseRestore(); #expect(model.restoreFile == nil)
    io.openURL = URL(fileURLWithPath: "/synthetic/restore.firrabackup")
    await model.chooseRestore(); io.fail = "read"; model.restorePhrase = "synthetic"
    await model.restore(); #expect(stub.requests.isEmpty && model.restorePhrase.isEmpty && model.message != nil)
  }

  @Test func diagnosticsAreSavedThroughThePanelAndOnlyAllowlistedFieldsAreExported() async throws {
    let io = FakeSettingsIO()
    let model = SettingsDataModel(api: api(StubTransport([.init(status: 200, body: diagnostics)])), actions: io.actions)
    await model.exportDiagnostics()
    #expect(io.filenames == ["firra-diagnostics.json"] && io.saved.count == 1)
    let body = try #require(JSONSerialization.jsonObject(with: io.saved[0].0) as? [String: Any])
    #expect(body["engine_version"] as? String == "test" && body["data_bytes"] as? Int == 512)
    #expect(body["unexpected_private_text"] == nil)
    let jobs = try #require(body["jobs"] as? [[String: Any]])
    #expect(jobs[0]["error_class"] is NSNull)
    let connections = try #require(body["connections"] as? [[String: Any]])
    #expect(connections[0]["last_error_class"] is NSNull && connections[0]["last_synced_at"] is NSNull)
  }

  @Test(arguments: ["server", "panel", "write"])
  func diagnosticsFailuresAndPanelCancellationNeverReportSaved(_ step: String) async {
    let io = FakeSettingsIO()
    if step == "panel" { io.saveURL = nil }
    if step == "write" { io.fail = "write" }
    let stub = StubTransport([.init(status: step == "server" ? 500 : 200, body: diagnostics)])
    let model = SettingsDataModel(api: api(stub), actions: io.actions)
    await model.exportDiagnostics()
    #expect(io.saved.isEmpty && model.message != "Diagnostics saved." && !model.busy)
  }

  @Test func copyFailureAndWrongFileTypeStayVisibleWithoutPersistingThePhrase() async {
    let io = FakeSettingsIO()
    var actions = io.actions; actions.copy = { _ in false }
    let model = SettingsDataModel(api: api(StubTransport([.init(status: 200, body: backup)])), actions: actions)
    await model.createBackup(); model.copyPhrase()
    #expect(model.message == "Could not copy the phrase.")
    io.openURL = URL(fileURLWithPath: "/synthetic/unrelated.json")
    await model.chooseRestore()
    #expect(model.restoreFile == nil && model.message == "Choose a .firrabackup file.")
    model.discardBackup(); #expect(model.recoveryPhrase == nil)
  }

  @Test func deletionEnforcesExactConfirmationAndCallsEngineBeforeLocalCleanup() async throws {
    let io = FakeSettingsIO()
    let stub = StubTransport([.init(status: 200, body: deleted)])
    var actions = io.actions
    actions.deleteKeychain = {
      #expect(stub.requests.count == 1 && stub.requests[0].url?.path == "/local/data")
      try io.step("keychain")
    }
    let model = SettingsDataModel(api: api(stub), actions: actions)
    for text in ["", "Delete everything", "delete everything "] {
      model.confirmation = text; await model.deleteEverything()
      #expect(stub.requests.isEmpty && io.events.isEmpty && !model.deletionStarted)
    }
    model.confirmation = "delete everything"; await model.deleteEverything()
    #expect(io.events == ["pause", "keychain", "stop", "files", "defaults", "quit"])
    #expect(model.deleteStep == .complete && model.deleteError == nil)
    let body = try #require(JSONSerialization.jsonObject(with: stub.requests[0].httpBody!) as? [String: String])
    #expect(body == ["confirm": "delete everything"] && stub.requests[0].httpMethod == "POST")
  }

  @Test(arguments: ["keychain", "stop", "files", "defaults"])
  func partialDeletionReportsWhatRemainsAndRetriesOnlyUnfinishedSteps(_ step: String) async {
    let io = FakeSettingsIO(); io.fail = step
    let stub = StubTransport([.init(status: 200, body: deleted)])
    let model = SettingsDataModel(api: api(stub), actions: io.actions)
    model.confirmation = "delete everything"; await model.deleteEverything()
    #expect(model.deleteError?.contains("did not finish") == true && !io.events.contains("quit"))
    #expect(model.remaining.contains(step == "defaults" ? "App settings" : step == "files" ? "local files" : step == "stop" ? "not stopped" : "Keychain"))
    let before = io.events
    io.fail = ""; await model.deleteEverything()
    #expect(model.deleteStep == .complete && io.events.last == "quit")
    #expect(stub.requests.count == 1 && io.events.filter { $0 == "pause" }.count == 1)
    #expect(io.events.prefix(before.count).elementsEqual(before))
  }

  @Test func failedEngineDeletionDoesNotRemoveLocalKeysOrFiles() async {
    let io = FakeSettingsIO()
    let stub = StubTransport([.init(status: 500, body: "{}"), .init(status: 200, body: deleted)])
    let model = SettingsDataModel(api: api(stub), actions: io.actions)
    model.confirmation = "delete everything"; await model.deleteEverything()
    #expect(io.events == ["pause"] && model.deleteStep == .engine && model.deleteError != nil)
    await model.deleteEverything()
    #expect(stub.requests.count == 2 && model.deleteStep == .complete)
  }

  @Test func aiSettingsLoadExistingChoiceAndStillRequireBackgroundAcknowledgement() async {
    let status = #"{"executor":"conversation_only","tools":[],"queue":{"pending":42},"state":"conversation_only"}"#
    let stub = StubTransport([.init(status: 200, body: #"{"executor":"conversation_only","backfill_months":6}"#),
      .init(status: 200, body: status),
      .init(status: 200, body: #"{"executor":"claude_code","backfill_months":6,"background_acknowledged":true}"#)])
    let model = AISetupModel(api: api(stub))
    await model.loadSettings(); #expect(!model.background && model.backfillMonths == 6 && model.waiting == "42 emails waiting")
    model.background = true; await model.save(backfillMonths: model.backfillMonths)
    #expect(stub.requests.count == 2 && !model.saved)
    model.acknowledged = true; await model.save(backfillMonths: model.backfillMonths)
    #expect(stub.requests.count == 3 && model.saved)
  }
}

@MainActor @Suite struct RetentionSettingsTests {
  func api(_ transport: any HTTPTransport) -> APIClient {
    APIClient(base: testBaseURL, transport: transport, tokenProvider: { "synthetic" })
  }
  func fixture() throws -> String {
    try String(contentsOf: #require(Bundle.module.url(forResource: "retention", withExtension: "json",
      subdirectory: "Fixtures/LocalRoutes")), encoding: .utf8)
  }
  func model(_ transport: any HTTPTransport) -> SettingsDataModel {
    SettingsDataModel(api: api(transport), actions: FakeSettingsIO().actions)
  }

  @Test func capturedDefaultIsTwelveAndDoesNotWrite() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture())])
    let model = model(stub)
    #expect(model.bodyMonths == 12 && !model.retentionLoaded)
    await model.requestRetention(3); await model.confirmRetention()
    #expect(stub.requests.isEmpty)
    await model.loadRetention()
    #expect(model.bodyMonths == 12 && model.retentionLoaded && !model.busy)
    #expect(stub.requests.count == 1 && stub.requests[0].httpMethod == "GET")
    #expect(stub.requests[0].url?.path == "/local/retention")
  }

  @Test func loadErrorPreservesDefaultAndRetryReadsUnlimited() async {
    let stub = StubTransport([.init(status: 500, body: #"{"message":"private details"}"#),
      .init(status: 200, body: #"{"body_months":null}"#)])
    let model = model(stub)
    await model.loadRetention()
    #expect(model.bodyMonths == 12 && !model.retentionLoaded && !model.busy)
    #expect(model.retentionError == "Retention could not be loaded. Try again.")
    await model.requestRetention(6); #expect(stub.requests.count == 1)
    await model.loadRetention()
    #expect(model.bodyMonths == nil && model.retentionLoaded && model.retentionError == nil)
  }

  @Test func shorteningRequiresConfirmationAndSendsAnInteger() async throws {
    let stub = StubTransport([.init(status: 200, body: try fixture()),
      .init(status: 200, body: #"{"body_months":6}"#)])
    let model = model(stub)
    await model.loadRetention(); await model.requestRetention(6)
    #expect(model.pendingRetentionMonths == 6 && model.bodyMonths == 12 && stub.requests.count == 1)
    await model.loadRetention(); #expect(stub.requests.count == 1)
    model.cancelRetention(); await model.confirmRetention()
    #expect(model.pendingRetentionMonths == nil && stub.requests.count == 1)
    await model.requestRetention(6); await model.confirmRetention(); await model.confirmRetention()
    #expect(model.bodyMonths == 6 && model.pendingRetentionMonths == nil && stub.requests.count == 2)
    let data = try #require(stub.requests[1].httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    let value = try #require(body["body_months"] as? NSNumber)
    #expect(CFGetTypeID(value) != CFBooleanGetTypeID() && value.intValue == 6)
    #expect(body.count == 1 && stub.requests[1].httpMethod == "PUT")
    #expect(stub.requests[1].url?.path == "/local/retention")
  }

  @Test func longerAndUnlimitedSaveWithoutConfirmationAndNullIsExplicit() async throws {
    let stub = StubTransport([.init(status: 200, body: #"{"body_months":6}"#),
      .init(status: 200, body: #"{"body_months":12}"#), .init(status: 200, body: #"{"body_months":null}"#)])
    let model = model(stub)
    await model.loadRetention(); await model.requestRetention(12)
    #expect(model.bodyMonths == 12 && model.pendingRetentionMonths == nil)
    await model.requestRetention(nil)
    #expect(model.bodyMonths == nil && model.pendingRetentionMonths == nil)
    let data = try #require(stub.requests[2].httpBody)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    #expect(body.count == 1 && body["body_months"] is NSNull)
    await model.requestRetention(nil); #expect(stub.requests.count == 3)
    await model.requestRetention(24)
    #expect(model.pendingRetentionMonths == 24 && stub.requests.count == 3) // unlimited -> finite is shorter
  }

  @Test(arguments: [false, true]) func failedSaveRevertsFiniteOrUnlimited(previousUnlimited: Bool) async {
    let initial = previousUnlimited ? #"{"body_months":null}"# : #"{"body_months":12}"#
    let stub = StubTransport([.init(status: 200, body: initial), .init(status: 500, body: "{}")])
    let model = model(stub)
    await model.loadRetention(); await model.requestRetention(6); await model.confirmRetention()
    #expect(model.bodyMonths == (previousUnlimited ? nil : 12))
    #expect(!model.busy && !model.retentionSaving && model.pendingRetentionMonths == nil)
    #expect(model.retentionError == "Retention could not be saved. Try again.")
    #expect(stub.requests.count == 2)
  }

  @Test(arguments: ["{}", #"{"body_months":"12"}"#, #"{"body_months":0}"#, #"{"body_months":121}"#])
  func invalidResponseNeverBecomesUnlimited(body: String) async {
    let model = model(StubTransport([.init(status: 200, body: body)]))
    await model.loadRetention()
    #expect(model.bodyMonths == 12 && !model.retentionLoaded && model.retentionError != nil)
  }

  @Test func storedCustomPeriodIsPreservedAndUnsupportedPickerValuesDoNotWrite() async {
    let stub = StubTransport([.init(status: 200, body: #"{"body_months":9}"#)])
    let model = model(stub)
    await model.loadRetention()
    #expect(model.bodyMonths == 9 && model.retentionLoaded)
    await model.requestRetention(9); await model.requestRetention(1); await model.requestRetention(0)
    #expect(stub.requests.count == 1 && model.pendingRetentionMonths == nil)
    await model.requestRetention(6); #expect(model.pendingRetentionMonths == 6)
  }

  @Test func busyProgressPreventsConcurrentSavingAndDeletion() async {
    let gate = GatedTransport(reply: .init(status: 200, body: #"{"body_months":24}"#))
    let model = model(gate)
    let load = Task { await model.loadRetention() }
    await gate.waitForRequests(1)
    #expect(model.busy && model.retentionLoading && !model.retentionLoaded)
    await model.loadRetention(); await model.requestRetention(3)
    #expect(gate.requestCount == 1)
    gate.releaseAll(); await load.value
    let save = Task { await model.requestRetention(nil) }
    await gate.waitForRequests(2)
    #expect(model.busy && model.retentionSaving && model.bodyMonths == nil)
    await model.loadRetention(); await model.requestRetention(3)
    model.confirmation = "delete everything"; await model.deleteEverything()
    #expect(gate.requestCount == 2 && !model.deletionStarted)
    gate.releaseAll(); await save.value
    #expect(!model.busy && !model.retentionSaving && model.bodyMonths == 24)
  }

  @Test func metricsAreSavedWithOnlyAllowlistedTopLevelFields() async throws {
    let io = FakeSettingsIO()
    let body = #"{"generated_at":"2026-10-06T00:00:00Z","engine_version":"test","as_of":"2026-10-06","usefulness":[{"dimension":"play","value":"p","n":2}],"precision":[],"lead_time":{"detected":1},"important_per_week":[{"week":"2026-10-05","count":2}],"client_name_leak":"Acme","note":"private"}"#
    let model = SettingsDataModel(api: api(StubTransport([.init(status: 200, body: body)])), actions: io.actions)
    await model.exportMetrics()
    #expect(io.filenames == ["firra-metrics.json"] && io.saved.count == 1)
    let saved = try #require(JSONSerialization.jsonObject(with: io.saved[0].0) as? [String: Any])
    #expect(saved["client_name_leak"] == nil && saved["note"] == nil)
    #expect(saved["engine_version"] as? String == "test")
    #expect((saved["important_per_week"] as? [[String: Any]])?.first?["count"] as? Int == 2)
    #expect(model.message?.hasPrefix("Metrics saved") == true)
  }

  @Test func metricsFailureNeverReportsSaved() async {
    let io = FakeSettingsIO()
    let model = SettingsDataModel(api: api(StubTransport([.init(status: 500, body: "{}")])), actions: io.actions)
    await model.exportMetrics()
    #expect(io.saved.isEmpty && model.message == "Metrics could not be exported. Try again." && !model.busy)
  }
}
