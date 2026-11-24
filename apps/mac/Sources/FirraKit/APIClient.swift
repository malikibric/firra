import Foundation

/// Typed access to the Firra API (`/v1`). The bearer token comes from `tokenProvider`, which
/// is supplied by the local engine handshake.
public struct APIClient: Sendable {
  private let base: URL
  private let transport: any HTTPTransport
  private let tokenProvider: @Sendable () async throws -> String
  private let onUnauthorized: (@Sendable () async -> Void)?

  /// `onUnauthorized` runs when the server rejects the access token, so the app can restart the engine handshake.
  public init(
    base: URL, transport: any HTTPTransport = URLSession.shared,
    tokenProvider: @escaping @Sendable () async throws -> String,
    onUnauthorized: (@Sendable () async -> Void)? = nil
  ) {
    self.base = base
    self.transport = transport
    self.tokenProvider = tokenProvider
    self.onUnauthorized = onUnauthorized
  }

  public func relaySettings() async throws -> RelaySettings { try await get("local/relay") }
  public func relaySettings(address: String) async throws -> RelaySettings {
    try await send("local/relay", method: "PUT", body: ["relay_url": address])
  }
  public func createRelayLink(name: String, sources: Bool) async throws -> RelayLinkCreated {
    try await send("local/relay/links", method: "POST",
      body: ["name": name, "scopes": sources ? ["intelligence", "sources"] : ["intelligence"]])
  }
  public func revokeRelayLink(_ id: String) async throws {
    let _: Empty = try await send("local/relay/links/\(id)", method: "DELETE", body: nil)
  }

  public func retentionSettings() async throws -> RetentionSettings { try await get("local/retention") }
  public func retentionSettings(bodyMonths: Int?) async throws -> RetentionSettings {
    try await send("local/retention", method: "PUT",
      body: ["body_months": bodyMonths.map { $0 as Any } ?? NSNull()])
  }

  public func createBackup() async throws -> BackupCreateResponse {
    try await send("local/backup/create", method: "POST", body: [:])
  }
  public func restoreBackup(_ data: Data, phrase: String) async throws -> BackupRestoreResponse {
    try await send("local/backup/restore", method: "POST",
      body: ["backup_b64": data.base64EncodedString(), "phrase": phrase])
  }
  public func exportDiagnostics() async throws -> Diagnostics {
    try await send("local/diagnostics/export", method: "POST", body: [:])
  }
  /// The engine's content-free metrics (usefulness, precision, lead time, important per week). Returned as raw JSON
  /// objects and filtered to an allowlist of top-level keys by the caller, like the diagnostics export.
  public func exportMetrics() async throws -> [String: Any] {
    let any: AnyJSON = try await send("local/metrics/export", method: "POST", body: [:])
    return any.value
  }
  public func deleteEverything() async throws -> DeleteDataResponse {
    try await send("local/data", method: "POST", body: ["confirm": "delete everything"])
  }
  public func aiSettings() async throws -> LocalAISettings { try await get("local/ai/settings") }

  public func mcpSettings() async throws -> McpSettings { try await get("local/mcp/settings") }
  public func mcpSettings(sourceAccess: Bool) async throws -> McpSettings {
    try await send("local/mcp/settings", method: "PUT", body: ["source_access": sourceAccess])
  }
  public func mcpSettings(processingTools: Bool) async throws -> McpSettings {
    try await send("local/mcp/settings", method: "PUT", body: ["processing_tools": processingTools])
  }

  public func mcpConnect() async throws -> McpConnect { try await get("local/mcp/connect") }

  public func syncConnection(_ id: String) async throws {
    let _: SyncStarted = try await send("local/connections/\(id)/sync", method: "POST", body: [:])
  }
  private struct SyncStarted: Decodable { let status: String }

  public func connectSlack(connectionId: String, accountLabel: String) async throws {
    let _: SlackConnected = try await send("local/connections/slack", method: "POST",
      body: ["connection_id": connectionId, "account_label": accountLabel])
  }
  private struct SlackConnected: Decodable { let connectionId: String; let teamId: String }

  public func slackChannels(_ id: String) async throws -> [SlackChannel] {
    let reply: SlackChannels = try await get("local/connections/slack/\(id)/channels")
    return reply.items
  }
  private struct SlackChannels: Decodable { let items: [SlackChannel] }

  public func saveSlackScope(_ id: String, channels: [SlackChannelChoice]) async throws {
    let _: SyncStarted = try await send("local/connections/slack/\(id)/scope", method: "PUT",
      body: ["channels": channels.map { choice -> [String: Any] in
        ["external_id": choice.channel.channelId, "label": choice.channel.label,
         "client_id": choice.clientId.map { $0 as Any } ?? NSNull(),
         "include": choice.included, "meta": ["kind": choice.channel.kind]]
      }])
  }

  public func disconnectSlack(_ id: String) async throws {
    let _: Empty = try await send("local/connections/slack/\(id)", method: "DELETE", body: nil)
  }

  public func localStatus() async throws -> LocalStatus {
    try await get("local/status")
  }

  @discardableResult
  public func connectGoogle(connectionId: String, accountLabel: String, backfillMonths: Int = 3) async throws -> String {
    let ids: GoogleConnectionIDs = try await send("local/connections/google", method: "POST",
      body: ["connection_id": connectionId, "account_label": accountLabel, "backfill_months": backfillMonths])
    return ids.gmailConnectionId
  }
  struct GoogleConnectionIDs: Decodable { let gmailConnectionId: String; let calendarConnectionId: String }

  public func onboardingSuggestions(connectionId: String? = nil) async throws -> OnboardingSuggestions {
    if let connectionId {
      let _: SuggestionStart = try await send("local/onboarding/suggestions", method: "POST",
        body: ["connection_id": connectionId])
    }
    return try await get("local/onboarding/suggestions")
  }
  private struct SuggestionStart: Decodable { let state: String }

  public func aiStatus() async throws -> LocalAIStatus { try await get("local/ai/status") }
  public func aiSettings(_ settings: LocalAISettings) async throws {
    let _: LocalAISettings = try await send("local/ai/settings", method: "PUT", body: [
      "executor": settings.executor, "backfill_months": settings.backfillMonths,
      "background_acknowledged": settings.backgroundAcknowledged,
    ])
  }

  public func me() async throws -> MeResponse {
    try await get("v1/me")
  }

  public func attention(workspaceId: String) async throws -> AttentionResponse {
    try await get("v1/workspaces/\(workspaceId)/attention")
  }

  public func changes(workspaceId: String, since: String? = nil) async throws -> ChangesResponse {
    try await send(
      "v1/workspaces/\(workspaceId)/changes", method: "GET", body: nil,
      query: since.map { [URLQueryItem(name: "since", value: $0)] } ?? [])
  }

  /// Pre-meeting briefs for client meetings that have not started, soonest first.
  public func upcomingMeetings(workspaceId: String) async throws -> MeetingsResponse {
    try await get("v1/workspaces/\(workspaceId)/meetings/upcoming")
  }

  public func routines(workspaceId: String) async throws -> RoutinesResponse {
    try await get("v1/workspaces/\(workspaceId)/routines")
  }

  public func setRoutineEnabled(workspaceId: String, kind: RoutineKind, enabled: Bool) async throws {
    let _: IdResponse = try await send(
      "v1/workspaces/\(workspaceId)/routines/\(kind.rawValue)", method: "PUT",
      body: ["enabled": enabled])
  }

  // MARK: setup (owner and admin)

  public func setupClients(workspaceId: String) async throws -> SetupClientsResponse {
    try await get("v1/workspaces/\(workspaceId)/setup/clients")
  }

  @discardableResult
  public func createClient(workspaceId: String, name: String, policy: ClientPolicy, domains: [String])
    async throws -> String
  {
    let response: IdResponse = try await send(
      "v1/workspaces/\(workspaceId)/clients", method: "POST",
      body: [
        "name": name, "policy": policy.rawValue, "domains": domains,
        "confirm_exclusion": policy == .excluded,
      ])
    return response.id
  }

  public func renameClient(workspaceId: String, clientId: String, name: String, status: String? = nil) async throws {
    var body = ["name": name]
    if let status { body["status"] = status }
    let _: Empty = try await send(
      "v1/workspaces/\(workspaceId)/clients/\(clientId)", method: "PATCH",
      body: body)
  }

  public func setClientPolicy(workspaceId: String, clientId: String, policy: ClientPolicy) async throws {
    let _: Empty = try await send(
      "v1/workspaces/\(workspaceId)/clients/\(clientId)/policy", method: "PUT",
      body: ["policy": policy.rawValue, "confirm_exclusion": policy == .excluded])
  }

  public func setClientDomains(workspaceId: String, clientId: String, domains: [String]) async throws {
    let _: Empty = try await send(
      "v1/workspaces/\(workspaceId)/clients/\(clientId)/domains", method: "PUT",
      body: ["domains": domains])
  }

  public func disconnect(workspaceId: String, connectionId: String) async throws {
    let _: Empty = try await send(
      "v1/workspaces/\(workspaceId)/connections/\(connectionId)", method: "DELETE", body: nil)
  }

  // MARK: services catalog (owner and admin)

  public func services(workspaceId: String) async throws -> ServicesResponse {
    try await get("v1/workspaces/\(workspaceId)/services")
  }

  public func createService(workspaceId: String, name: String, description: String, keywords: [String])
    async throws
  {
    let _: IdResponse = try await send(
      "v1/workspaces/\(workspaceId)/services", method: "POST",
      body: ["name": name, "description": description, "keywords": keywords])
  }

  public func saveService(
    workspaceId: String, serviceId: String, name: String, description: String, keywords: [String]
  ) async throws {
    let _: IdResponse = try await send(
      "v1/workspaces/\(workspaceId)/services/\(serviceId)", method: "PUT",
      body: ["name": name, "description": description, "keywords": keywords])
  }

  public func deleteService(workspaceId: String, serviceId: String) async throws {
    let _: Empty = try await send(
      "v1/workspaces/\(workspaceId)/services/\(serviceId)", method: "DELETE", body: nil)
  }


  struct IdResponse: Decodable { let id: String }

  // MARK: client detail, evidence, decisions (owner and admin)

  public func clientDetail(workspaceId: String, clientId: String) async throws -> ClientDetail {
    let reply: ClientDetailResponse = try await get("v1/workspaces/\(workspaceId)/clients/\(clientId)")
    return reply.client
  }

  public func clientEvidence(workspaceId: String, clientId: String, cursor: String? = nil) async throws
    -> EvidencePage
  {
    try await send(
      "v1/workspaces/\(workspaceId)/clients/\(clientId)/evidence", method: "GET", body: nil,
      query: [URLQueryItem(name: "limit", value: "100")]
        + (cursor.map { [URLQueryItem(name: "cursor", value: $0)] } ?? []))
  }

  public func decisions(
    workspaceId: String, status: String = "new", importance: String? = nil,
    kind: String? = nil, cursor: String? = nil
  ) async throws
    -> DecisionsPage
  {
    try await send(
      "v1/workspaces/\(workspaceId)/decisions", method: "GET", body: nil,
      query: [URLQueryItem(name: "status", value: status), URLQueryItem(name: "limit", value: "50")]
        + (importance.map { [URLQueryItem(name: "importance", value: $0)] } ?? [])
        + (kind.map { [URLQueryItem(name: "kind", value: $0)] } ?? [])
        + (cursor.map { [URLQueryItem(name: "cursor", value: $0)] } ?? []))
  }

  public func clientTimeline(workspaceId: String, clientId: String, cursor: String? = nil) async throws
    -> TimelinePage
  {
    try await send(
      "v1/workspaces/\(workspaceId)/clients/\(clientId)/timeline", method: "GET", body: nil,
      query: [URLQueryItem(name: "limit", value: "50")]
        + (cursor.map { [URLQueryItem(name: "cursor", value: $0)] } ?? []))
  }

  public func evidenceItem(workspaceId: String, evidenceId: String) async throws -> EvidenceItem {
    try await get("v1/workspaces/\(workspaceId)/evidence/\(evidenceId)")
  }

  public func sendFeedback(workspaceId: String, cardId: String, verdict: FeedbackVerdict, note: String?)
    async throws
  {
    var body: [String: Any] = ["verdict": verdict.rawValue]
    if let note, !note.isEmpty { body["note"] = note }
    let _: IdResponse = try await send(
      "v1/workspaces/\(workspaceId)/decisions/\(cardId)/feedback", method: "POST", body: body)
  }

  public func recordOutcome(
    workspaceId: String, clientId: String, event: OutcomeEvent, occurredAt: String
  ) async throws {
    let _: IdResponse = try await send(
      "v1/workspaces/\(workspaceId)/clients/\(clientId)/outcomes", method: "POST",
      body: ["event": event.rawValue, "occurred_at": occurredAt])
  }

  // MARK: activity and replay

  public func audit(workspaceId: String, page: Int = 1) async throws -> AuditResponse {
    try await send(
      "v1/workspaces/\(workspaceId)/audit", method: "GET", body: nil,
      query: [URLQueryItem(name: "page", value: String(page))])
  }

  /// Asks for a replay of stored history over a window (dates as yyyy-MM-dd).
  public func requestReplay(workspaceId: String, start: String, end: String, stepDays: Int) async throws {
    let _: IdResponse = try await send(
      "v1/workspaces/\(workspaceId)/replay/runs", method: "POST",
      body: ["window_start": start, "window_end": end, "step_days": stepDays])
  }

  public func replayRuns(workspaceId: String) async throws -> [ReplayRunItem] {
    let reply: ReplayRunsResponse = try await get("v1/workspaces/\(workspaceId)/replay/runs")
    return reply.items
  }

  public func replayClient(workspaceId: String, runId: String, clientId: String) async throws
    -> ReplayClientResult
  {
    try await get("v1/workspaces/\(workspaceId)/replay/runs/\(runId)/clients/\(clientId)")
  }

  public func allClients(workspaceId: String) async throws -> [ClientListItem] {
    try await clients(workspaceId: workspaceId, status: "all")
  }

  public func clients(workspaceId: String, status: String = "active") async throws -> [ClientListItem] {
    let reply: ClientListResponse = try await send(
      "v1/workspaces/\(workspaceId)/clients", method: "GET", body: nil,
      query: [URLQueryItem(name: "status", value: status)])
    return reply.items
  }

  struct Empty: Decodable {}

  private func get<T: Decodable>(_ path: String) async throws -> T {
    try await send(path, method: "GET", body: nil)
  }

  private func send<T: Decodable>(
    _ path: String, method: String, body: [String: Any]?, query: [URLQueryItem] = []
  ) async throws -> T {
    var url = base.appending(path: path)
    if !query.isEmpty {
      var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
      parts.queryItems = query
      url = parts.url!
    }
    var request = URLRequest(url: url)
    request.httpMethod = method
    request.setValue("Bearer \(try await tokenProvider())", forHTTPHeaderField: "Authorization")
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    if let body {
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONSerialization.data(withJSONObject: body)
    }
    let data: Data
    let response: HTTPURLResponse
    do { (data, response) = try await transport.send(request) } catch let error as APIError {
      throw error
    } catch { throw APIError.transport(error.localizedDescription) }

    if response.statusCode == 204, let empty = Empty() as? T { return empty }
    if (200..<300).contains(response.statusCode) {
      do { return try JSONDecoder.api.decode(T.self, from: data) } catch {
        throw APIError.transport("unexpected answer shape")
      }
    }
    if response.statusCode == 401 {
      await onUnauthorized?()
      throw APIError.unauthenticated
    }
    if let body = try? JSONDecoder.api.decode(APIErrorBody.self, from: data) {
      throw APIError.server(
        status: response.statusCode, code: body.error, message: body.message,
        requestID: body.requestId)
    }
    throw APIError.transport("status \(response.statusCode)")
  }
}

/// A decoded JSON object of unknown shape, so an export can be filtered by key without modelling every field.
struct AnyJSON: Decodable {
  let value: [String: Any]
  init(from decoder: Decoder) throws {
    let container = try decoder.singleValueContainer()
    guard let object = try? container.decode([String: JSONLeaf].self) else {
      throw DecodingError.dataCorruptedError(in: container, debugDescription: "not an object")
    }
    value = object.mapValues(\.any)
  }
}
private struct JSONLeaf: Decodable {
  let any: Any
  init(from decoder: Decoder) throws {
    let c = try decoder.singleValueContainer()
    if c.decodeNil() { any = NSNull() }
    else if let v = try? c.decode(Bool.self) { any = v }
    else if let v = try? c.decode(Int.self) { any = v }
    else if let v = try? c.decode(Double.self) { any = v }
    else if let v = try? c.decode(String.self) { any = v }
    else if let v = try? c.decode([JSONLeaf].self) { any = v.map(\.any) }
    else if let v = try? c.decode([String: JSONLeaf].self) { any = v.mapValues(\.any) }
    else { throw DecodingError.dataCorruptedError(in: c, debugDescription: "unsupported") }
  }
}
