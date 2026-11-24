import Foundation

public struct UserProfile: Codable, Sendable, Equatable {
  public let id: String
  public let email: String?
}

// Types mirror packages/contracts/src/v1 (snake_case on the wire, converted by the decoder).

public struct Membership: Codable, Sendable, Equatable, Identifiable {
  public let workspaceId: String
  public let workspaceName: String
  public let role: String
  public var id: String { workspaceId }
}

public struct MeResponse: Codable, Sendable, Equatable {
  public let user: UserProfile
  public let memberships: [Membership]
}

public struct AttentionDriver: Codable, Sendable, Equatable {
  public let type: String
  public let family: String?
  public let severity: String
  public let basis: String
  public let primary: Bool
  public let evidenceCount: Int
}

public struct AttentionItem: Codable, Sendable, Equatable, Identifiable {
  public let clientId: String
  public let name: String
  public let status: String
  public let policy: String
  public let asOf: String
  public let riskState: String
  public let previousRiskState: String?
  public let stateSince: String
  public let expansionState: String
  public let strength: String
  public let play: String?
  public let daysToNotice: Int?
  public let drivers: [AttentionDriver]
  public var id: String { clientId }
}

public struct AttentionResponse: Codable, Sendable, Equatable {
  public let generatedAt: String
  public let items: [AttentionItem]
}

public enum ClientPolicy: String, Codable, Sendable, CaseIterable {
  case full, metadataOnly = "metadata_only", excluded

  public var label: String {
    switch self {
    case .full: "Full"
    case .metadataOnly: "Metadata only"
    case .excluded: "Excluded"
    }
  }

  public var explanation: String {
    switch self {
    case .full: "Messages are read and analysed."
    case .metadataOnly: "Only who and when is kept. No text is stored or analysed."
    case .excluded: "Nothing about this client is kept. Existing records are deleted."
    }
  }
}

public struct SetupClient: Decodable, Sendable, Equatable, Identifiable {
  public let id: String
  public let name: String
  public let status: String
  public let policy: ClientPolicy
  public let domains: [String]
}

public struct SetupClientsResponse: Decodable, Sendable, Equatable {
  public let items: [SetupClient]
}

public struct ServiceItem: Decodable, Sendable, Equatable, Identifiable {
  public let id: String
  public let name: String
  public let description: String
  public let keywords: [String]
}

public struct ServicesResponse: Decodable, Sendable, Equatable {
  public let items: [ServiceItem]
}

public struct AuditEntry: Decodable, Sendable, Equatable, Identifiable {
  public let id: String
  public let action: String
  public let actorType: String
  public let actorId: String?
  public let targetType: String?
  public let createdAt: String
}

public struct AuditResponse: Decodable, Sendable, Equatable {
  public let items: [AuditEntry]
  public let nextPage: Int?
}

// MARK: client detail, evidence, decisions

/// A number, text, flag or nothing: the values of a metadata driver's inputs.
public enum Scalar: Decodable, Sendable, Equatable {
  case number(Double), text(String), flag(Bool), none

  public init(from decoder: Decoder) throws {
    let value = try decoder.singleValueContainer()
    if value.decodeNil() { self = .none }
    else if let flag = try? value.decode(Bool.self) { self = .flag(flag) }
    else if let number = try? value.decode(Double.self) { self = .number(number) }
    else { self = .text(try value.decode(String.self)) }
  }

  public var display: String {
    switch self {
    case .number(let n): n.rounded() == n ? String(Int(n)) : String(format: "%.1f", n)
    case .text(let t): t.count > 10 && t.contains("T") ? String(t.prefix(10)) : t
    case .flag(let f): f ? "yes" : "no"
    case .none: "none"
    }
  }
}

public struct DetailDriver: Decodable, Sendable, Equatable {
  public let type: String
  public let family: String?
  public let severity: String
  public let basis: String
  public let primary: Bool
  public let evidenceIds: [String]
  public let occurredAt: String?
  /// For drivers computed from message counts and dates (no quotes): the numbers behind them.
  public let inputs: [String: Scalar]?

  /// The inputs as short readable lines, in a stable order; ids are left out.
  public var explanation: [String] {
    guard let inputs else { return [] }
    let labels: [String: String] = [
      "messages_30": "messages in the last 30 days", "initiated_30": "conversations they started (30 days)",
      "baseline_per_30": "usual per 30 days", "recent_median_h": "reply time now (hours, median)",
      "base_median_h": "usual reply time (hours)", "authors_60": "people writing (60 days)",
      "baseline_authors_per_60": "usual people writing", "affected": "affected", "total": "total",
      "last_message_at": "last message", "renewal_date": "renewal date", "notice_deadline": "notice deadline",
      "days_to_notice": "days to notice", "contract_start": "contract start", "days": "days",
      "mean_90": "average (90 days)", "base_mean": "usual average",
    ]
    return inputs.keys.sorted().compactMap { key in
      guard key != "person_id", let value = inputs[key] else { return nil }
      return "\(labels[key] ?? key.replacingOccurrences(of: "_", with: " ")): \(value.display)"
    }
  }
}

public struct DetailCaveat: Decodable, Sendable, Equatable {
  public let code: String
  public let detail: String
}

public struct DetailAssessment: Decodable, Sendable, Equatable {
  public let riskState: String
  public let previousRiskState: String?
  public let stateSince: String
  public let expansionState: String
  public let play: String?
  public let strength: String
  public let asOf: String
  public let caveats: [DetailCaveat]
  public let drivers: [DetailDriver]
}

public struct StateChange: Decodable, Sendable, Equatable {
  public let asOf: String
  public let from: String
  public let to: String
}

public struct Stakeholder: Decodable, Sendable, Equatable {
  public let displayName: String?
  public let roleTitle: String?
  public let isDecisionMaker: Bool
  public let lastSeenAt: String?
  public let isChampion: Bool

  private enum CodingKeys: String, CodingKey {
    case displayName, roleTitle, isDecisionMaker, lastSeenAt, isChampion
  }

  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    displayName = try values.decodeIfPresent(String.self, forKey: .displayName)
    roleTitle = try values.decodeIfPresent(String.self, forKey: .roleTitle)
    isDecisionMaker = try values.decode(Bool.self, forKey: .isDecisionMaker)
    lastSeenAt = try values.decodeIfPresent(String.self, forKey: .lastSeenAt)
    isChampion = try values.decodeIfPresent(Bool.self, forKey: .isChampion) ?? false
  }
}

public struct GoalItem: Decodable, Sendable, Equatable {
  public let text: String
  public let metric: String?
  public let statedAt: String
  public let evidenceIds: [String]

  private enum CodingKeys: String, CodingKey {
    case text, metric, statedAt, evidenceIds
  }

  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    text = try values.decode(String.self, forKey: .text)
    metric = try values.decodeIfPresent(String.self, forKey: .metric)
    statedAt = try values.decode(String.self, forKey: .statedAt)
    evidenceIds = try values.decodeIfPresent([String].self, forKey: .evidenceIds) ?? []
  }
}

public struct CommitmentItem: Decodable, Sendable, Equatable {
  public let ownerSide: String
  public let text: String
  public let dueDate: String?
  public let status: String
  public let evidenceIds: [String]

  private enum CodingKeys: String, CodingKey {
    case ownerSide, text, dueDate, status, evidenceIds
  }

  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    ownerSide = try values.decode(String.self, forKey: .ownerSide)
    text = try values.decode(String.self, forKey: .text)
    dueDate = try values.decodeIfPresent(String.self, forKey: .dueDate)
    status = try values.decode(String.self, forKey: .status)
    evidenceIds = try values.decodeIfPresent([String].self, forKey: .evidenceIds) ?? []
  }
}

public struct NeedItem: Decodable, Sendable, Equatable {
  public let topic: String?
  public let mentionCount: Int
  public let threadCount: Int
  public let lastSeenAt: String
  public let evidenceIds: [String]

  private enum CodingKeys: String, CodingKey {
    case topic, mentionCount, threadCount, lastSeenAt, evidenceIds
  }

  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    topic = try values.decodeIfPresent(String.self, forKey: .topic)
    mentionCount = try values.decode(Int.self, forKey: .mentionCount)
    threadCount = try values.decode(Int.self, forKey: .threadCount)
    lastSeenAt = try values.decode(String.self, forKey: .lastSeenAt)
    evidenceIds = try values.decodeIfPresent([String].self, forKey: .evidenceIds) ?? []
  }
}

public struct OutcomeItem: Decodable, Sendable, Equatable {
  public let event: String
  public let occurredAt: String
}

public struct Unknown: Decodable, Sendable, Equatable {
  public let code: String
  public let detail: String
}

public struct ClientDetail: Decodable, Sendable, Equatable {
  public let id: String
  public let name: String
  public let status: String
  public let policy: String
  public let mrr: Double?
  public let currency: String?
  public let renewalDate: String?
  public let noticePeriodDays: Int?
  public let assessment: DetailAssessment?
  public let stateHistory: [StateChange]
  public let stakeholders: [Stakeholder]
  public let commitments: [CommitmentItem]
  public let goals: [GoalItem]
  public let needs: [NeedItem]
  public let outcomes: [OutcomeItem]
  public let unknowns: [Unknown]

  private enum CodingKeys: String, CodingKey {
    case id, name, status, policy, mrr, currency, renewalDate, noticePeriodDays, assessment, stateHistory, stakeholders, commitments, goals, needs, outcomes, unknowns
  }

  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    id = try values.decode(String.self, forKey: .id)
    name = try values.decode(String.self, forKey: .name)
    status = try values.decode(String.self, forKey: .status)
    policy = try values.decode(String.self, forKey: .policy)
    mrr = try values.decodeIfPresent(Double.self, forKey: .mrr)
    currency = try values.decodeIfPresent(String.self, forKey: .currency)
    renewalDate = try values.decodeIfPresent(String.self, forKey: .renewalDate)
    noticePeriodDays = try values.decodeIfPresent(Int.self, forKey: .noticePeriodDays)
    assessment = try values.decodeIfPresent(DetailAssessment.self, forKey: .assessment)
    stateHistory = try values.decode([StateChange].self, forKey: .stateHistory)
    stakeholders = try values.decode([Stakeholder].self, forKey: .stakeholders)
    commitments = try values.decode([CommitmentItem].self, forKey: .commitments)
    goals = try values.decode([GoalItem].self, forKey: .goals)
    needs = try values.decode([NeedItem].self, forKey: .needs)
    outcomes = try values.decode([OutcomeItem].self, forKey: .outcomes)
    unknowns = try values.decodeIfPresent([Unknown].self, forKey: .unknowns) ?? []
  }
}

public struct ClientDetailResponse: Decodable, Sendable, Equatable {
  public let client: ClientDetail
}

/// One verified quote with where it came from. The text is the client's or the firm's own words,
/// shown as data, never as an instruction.
public struct EvidenceItem: Decodable, Sendable, Equatable, Identifiable {
  public let id: String
  public let quote: String
  public let deepLink: String?
  public let occurredAt: String
  public let authorName: String?
  public let authorRole: String?
  public let subject: String?
  /// The first 8 characters of the source record id, for tracing quoted evidence.
  public let sourceRef: String?
}

public struct EvidencePage: Decodable, Sendable, Equatable {
  public let items: [EvidenceItem]
  public let nextCursor: String?
}

public struct DecisionFeedback: Decodable, Sendable, Equatable {
  public let verdict: String
  public let action: String?
  public let note: String?
}

public struct DecisionItem: Decodable, Sendable, Equatable, Identifiable {
  public let id: String
  public let clientId: String
  public let clientName: String
  public let week: String
  public let kind: String
  public let importance: String?
  public let surfacedOn: String?
  public let title: String
  public let body: String
  public let play: String
  public let strength: String
  public let status: String
  public let evidenceIds: [String]
  public let feedback: DecisionFeedback?
}

public struct DecisionsPage: Decodable, Sendable, Equatable {
  public let items: [DecisionItem]
  public let nextCursor: String?
}

public enum FeedbackVerdict: String, Sendable, CaseIterable {
  case useful, alreadyKnown = "already_known", wrong, notNow = "not_now"

  public var label: String {
    switch self {
    case .useful: "Useful"
    case .alreadyKnown: "Already knew"
    case .wrong: "Wrong"
    case .notNow: "Not now"
    }
  }
}

public enum OutcomeEvent: String, Sendable, CaseIterable {
  case renewed, priceIncrease = "price_increase", downgrade, expanded, churned, referral

  public var label: String { rawValue.replacingOccurrences(of: "_", with: " ").capitalized }
}

// MARK: replay results

public struct ReplayTotals: Decodable, Sendable, Equatable {
  public let losses: Int
  /// `losses_detected_30d` (the snake-case conversion capitalizes the D).
  public let lossesDetected30D: Int
  public let medianLeadDays: Double?
  public let retained: Int
  public let retainedWithElevated: Int
  public let falseAlarmClientWeeksPer10ClientsPerMonth: Double
}

public struct ReplayRunItem: Decodable, Sendable, Equatable, Identifiable {
  public let id: String
  public let windowStart: String
  public let windowEnd: String
  public let stepDays: Int
  public let status: String
  public let finishedAt: String?
  public let totals: ReplayTotals?
}

public struct ReplayRunsResponse: Decodable, Sendable, Equatable {
  public let items: [ReplayRunItem]
}

public struct ReplayTransition: Decodable, Sendable, Equatable {
  public let asOf: String
  public let fromState: String?
  public let toState: String
  public let play: String
  public let daysBeforeEnd: Int?
}

public struct ReplayNarrative: Decodable, Sendable, Equatable {
  public let status: String
  public let endDate: String?
  public let endKind: String?
  public let detectionAt: String?
  public let detectionState: String?
  public let leadDays: Int?
  public let maxState: String?
  public let weeksElevated: Int
  public let transitions: [ReplayTransition]
  public let lastState: String?
  public let caveats: [String]
}

public struct ReplayWeek: Decodable, Sendable, Equatable {
  public let asOf: String
  public let risk: String
  public let expansion: String
  public let play: String?
}

public struct ReplayClientResult: Decodable, Sendable, Equatable {
  public let runId: String
  public let clientId: String
  public let narrative: ReplayNarrative
  public let weeks: [ReplayWeek]
}

public struct ClientListItem: Decodable, Sendable, Equatable, Identifiable {
  public let id: String
  public let name: String
  public let status: String
  public let policy: String
}

public struct ClientListResponse: Decodable, Sendable, Equatable {
  public let items: [ClientListItem]
}

/// One dated event of a client: a change of state, a signal, a commitment or an outcome.
public struct TimelineEvent: Decodable, Sendable, Equatable, Identifiable {
  public let id: String
  public let occurredAt: String
  public let kind: String
  public let from: String?
  public let to: String?
  public let type: String?
  public let change: String?
  public let severity: String?
  public let ownerSide: String?
  public let status: String?
  public let event: String?
  public let evidenceIds: [String]?

  /// One line a founder can read.
  public var summary: String {
    let pretty = { (raw: String?) in (raw ?? "").replacingOccurrences(of: "_", with: " ") }
    switch kind {
    case "state_change": return "Risk \(from ?? "?") → \(to ?? "?")"
    case "signal": return "\(pretty(type).capitalized) \(pretty(change)) (\(severity ?? ""))"
    case "commitment":
      return (ownerSide == "firm" ? "Our" : "Their") + " commitment \(pretty(change)) (\(pretty(status)))"
    case "outcome": return "Outcome: \(pretty(event))"
    default: return pretty(kind)
    }
  }
}

public struct TimelinePage: Decodable, Sendable, Equatable {
  public let items: [TimelineEvent]
  public let nextCursor: String?
}

/// The part of `/v1/workspaces/:id/meetings/upcoming` the notch pill shows: who, when and the questions.
public struct MeetingsResponse: Decodable, Sendable, Equatable {
  public struct Question: Decodable, Sendable, Equatable { public let text: String }
  public struct Item: Decodable, Sendable, Equatable {
    public let id: String
    public let clientId: String
    public let clientName: String
    public let startsAt: String
    public let questions: [Question]
  }
  public let items: [Item]
}

public struct ChangeItem: Decodable, Sendable, Equatable {
  public let clientId: String
  public let clientName: String
  public let asOf: String
  public let kind: String
  public let from: String?
  public let to: String?
  public let signalType: String?
  public let family: String?
  public let severity: String?
  public let evidenceCount: Int

  public var summary: String {
    let signal = (signalType ?? "").replacingOccurrences(of: "_", with: " ").capitalized
    let level = severity.map { " (\($0))" } ?? ""
    switch kind {
    case "risk_state_changed": return "Risk \(from ?? "?") → \(to ?? "?")"
    case "expansion_state_changed": return "Expansion \(from ?? "?") → \(to ?? "?")"
    case "signal_opened": return "\(signal) signal opened\(level)"
    case "signal_escalated": return "\(signal) signal escalated\(level)"
    case "signal_resolved": return "\(signal) signal resolved"
    default: return "Change recorded"
    }
  }
}

public struct ChangesResponse: Decodable, Sendable, Equatable {
  public let generatedAt: String
  public let since: String
  public let items: [ChangeItem]
}

// Server-owned routine settings; never persisted on this Mac.
public enum RoutineKind: String, Decodable, Sendable, CaseIterable {
  case morningScan = "morning_scan"
  case commitmentWatch = "commitment_watch"
  case preMeetingBrief = "pre_meeting_brief"
}

public struct RoutineLastRun: Decodable, Sendable, Equatable {
  public let scheduledFor: String
  public let status: String
  public let finishedAt: String?
  public let counts: [String: Double]

  public var displayStatus: String {
    ["queued", "running", "succeeded", "failed"].contains(status) ? status : "unknown"
  }
}

public struct RoutineItem: Decodable, Sendable, Equatable, Identifiable {
  public let kind: RoutineKind
  public internal(set) var enabled: Bool
  public let created: Bool
  public let lastRun: RoutineLastRun?
  public var id: RoutineKind { kind }
}

public struct RoutinesResponse: Decodable, Sendable, Equatable {
  public let generatedAt: String
  public let items: [RoutineItem]
}
