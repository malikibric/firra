import FirraKit
import SwiftUI

/// One client: where things stand, why, and what is open. Every driver shows the quotes behind it.
struct ClientDetailView: View {
  @Environment(NotificationFocus.self) private var notificationFocus
  let model: ClientDetailModel
  let workspaceId: String
  let clientId: String
  let name: String
  @State private var recording = false

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 18) {
        switch model.phase {
        case .idle, .loading:
          ProgressView().frame(maxWidth: .infinity)
        case .failed(let text):
          ContentUnavailableView {
            Label("Could not load", systemImage: "exclamationmark.triangle")
          } description: {
            Text(text)
          } actions: {
            Button("Try again") { Task { await model.load(workspaceId: workspaceId, clientId: clientId) } }
          }
        case .ready:
          if let detail = model.detail { content(detail) }
        }
      }
      .padding()
      .frame(maxWidth: .infinity, alignment: .leading)
    }
    .onAppear { notificationFocus.clientIDs.insert(clientId) }
    .onDisappear { notificationFocus.clientIDs.remove(clientId) }
    .navigationTitle(name)
    .task(id: clientId) { await model.load(workspaceId: workspaceId, clientId: clientId) }
    .sheet(isPresented: $recording) {
      OutcomeSheet(model: model, workspaceId: workspaceId, clientId: clientId)
    }
  }

  @ViewBuilder
  private func content(_ detail: ClientDetail) -> some View {
    header(detail)
    why(detail)
    facts(detail)
    people(detail)
    if !detail.unknowns.isEmpty {
      section("What we don't know yet") {
        ForEach(Array(detail.unknowns.enumerated()), id: \.offset) { _, unknown in
          Text(unknown.detail)
        }
      }
    }
    events(detail)
    HStack {
      Button("Record what happened…") { recording = true }
      if let message = model.message { Text(message).font(.callout).foregroundStyle(.secondary) }
    }
  }

  @ViewBuilder
  private func why(_ detail: ClientDetail) -> some View {
    if let assessment = detail.assessment {
      section("Why") {
        ForEach(Array(assessment.drivers.enumerated()), id: \.offset) { _, driver in
          driverRow(driver)
        }
        ForEach(Array(assessment.caveats.enumerated()), id: \.offset) { _, caveat in
          Label(caveat.detail, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
        }
      }
    } else {
      Text("No assessment yet. Firra needs more history for this client.").foregroundStyle(.secondary)
    }
  }

  @ViewBuilder
  private func driverRow(_ driver: DetailDriver) -> some View {
    if model.evidenceMissing(for: driver) {
      VStack(alignment: .leading, spacing: 4) {
        Text(driver.type.replacingOccurrences(of: "_", with: " ").capitalized).bold()
        Label(
          "The quotes behind this could not be loaded, so Firra does not show it as a finding.",
          systemImage: "exclamationmark.triangle"
        )
        .font(.callout).foregroundStyle(.secondary)
        Button("Retry") { Task { await model.load(workspaceId: workspaceId, clientId: clientId) } }
          .buttonStyle(.link)
      }
    } else {
      DriverBlock(driver: driver, quotes: model.quotes(for: driver))
    }
  }

  @ViewBuilder
  private func facts(_ detail: ClientDetail) -> some View {
    if !detail.commitments.isEmpty {
      section("Open commitments") {
        ForEach(Array(detail.commitments.enumerated()), id: \.offset) { _, item in
          EvidenceRow(model: model, evidenceIds: item.evidenceIds, workspaceId: workspaceId) {
            HStack(alignment: .top) {
              Text(item.ownerSide == "firm" ? "We" : "They").font(.caption.bold()).frame(width: 36, alignment: .leading)
              Text(item.text)
              Spacer()
              Text((item.status == "late" ? "Late · " : "") + (item.dueDate ?? "no date"))
                .font(.caption).foregroundStyle(item.status == "late" ? .red : .secondary)
            }
          }
        }
      }
    }
    if !detail.goals.isEmpty {
      section("Goals they stated") {
        ForEach(Array(detail.goals.enumerated()), id: \.offset) { _, goal in
          EvidenceRow(model: model, evidenceIds: goal.evidenceIds, workspaceId: workspaceId) {
            Text(Self.goalLine(goal))
          }
        }
      }
    }
    if !detail.needs.isEmpty {
      section("Recurring asks") {
        ForEach(Array(detail.needs.enumerated()), id: \.offset) { _, need in
          EvidenceRow(model: model, evidenceIds: need.evidenceIds, workspaceId: workspaceId) {
            Text("\(need.topic ?? "Unnamed topic") — \(need.mentionCount) mentions in \(need.threadCount) threads")
          }
        }
      }
    }
  }

  @ViewBuilder
  private func people(_ detail: ClientDetail) -> some View {
    if !detail.stakeholders.isEmpty {
      section("People") {
        ForEach(Array(detail.stakeholders.enumerated()), id: \.offset) { _, person in
          Text(Self.personLine(person))
        }
      }
    }
  }

  @ViewBuilder
  private func events(_ detail: ClientDetail) -> some View {
    if model.timelineFailed {
      Label("Recent events could not be loaded.", systemImage: "exclamationmark.triangle")
        .font(.callout).foregroundStyle(.secondary)
    }
    if !model.timeline.isEmpty {
      section("Recent events") {
        ForEach(model.timeline) { event in
          TimelineEventRow(model: model, event: event, workspaceId: workspaceId)
        }
      }
    }
    if model.canLoadMoreTimeline {
      Button("Show older events") {
        Task { await model.loadMoreTimeline(workspaceId: workspaceId, clientId: clientId) }
      }
      .disabled(model.isLoadingTimeline)
    }
    if !detail.stateHistory.isEmpty || !detail.outcomes.isEmpty {
      section("History") {
        ForEach(Array(detail.stateHistory.enumerated()), id: \.offset) { _, change in
          Text("\(change.asOf): \(change.from) → \(change.to)")
        }
        ForEach(Array(detail.outcomes.enumerated()), id: \.offset) { _, outcome in
          Text("\(outcome.occurredAt): \(outcome.event.replacingOccurrences(of: "_", with: " "))")
        }
      }
    }
  }

  private static func goalLine(_ goal: GoalItem) -> String {
    goal.text + (goal.metric.map { " (\($0))" } ?? "") + " · " + String(goal.statedAt.prefix(10))
  }

  private static func personLine(_ person: Stakeholder) -> String {
    var line = person.displayName ?? "Unknown"
    if let role = person.roleTitle { line += " · " + role }
    if person.isChampion { line += " · champion" }
    if person.isDecisionMaker { line += " · decision maker" }
    if let seen = person.lastSeenAt { line += " · last seen " + String(seen.prefix(10)) }
    return line
  }

  private func header(_ detail: ClientDetail) -> some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(detail.name).font(.title.bold())
      let parts = [
        detail.assessment.map { "Risk: \($0.riskState) since \($0.stateSince)" },
        detail.assessment.flatMap { $0.play.map { "Suggested: " + $0.replacingOccurrences(of: "_", with: " ") } },
        detail.mrr.map { "\(Int($0)) \(detail.currency ?? "")/month" },
        detail.renewalDate.map { "Renews \($0)" },
        detail.policy == "metadata_only" ? "Metadata only: no quotes" : nil,
      ].compactMap { $0 }
      Text(parts.joined(separator: " · ")).foregroundStyle(.secondary)
    }
  }

  private func section(_ title: String, @ViewBuilder _ body: () -> some View) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(title).font(.headline)
      body()
    }
  }
}

private struct EvidenceRow<Content: View>: View {
  let model: ClientDetailModel
  let evidenceIds: [String]
  let workspaceId: String
  @ViewBuilder let content: () -> Content
  @State private var expanded = false

  var body: some View {
    if evidenceIds.isEmpty {
      content()
    } else {
      DisclosureGroup(isExpanded: $expanded) {
        ForEach(model.quotes(evidenceIds: evidenceIds)) { quote in QuoteView(quote: quote) }
      } label: {
        content()
      }
      .task(id: expanded) {
        if expanded { await model.loadEvidence(workspaceId: workspaceId, evidenceIds: evidenceIds) }
      }
    }
  }
}

private struct TimelineEventRow: View {
  let model: ClientDetailModel
  let event: TimelineEvent
  let workspaceId: String
  @State private var expanded = false

  var body: some View {
    if let ids = event.evidenceIds, !ids.isEmpty {
      DisclosureGroup(isExpanded: $expanded) {
        ForEach(model.quotes(for: event)) { quote in QuoteView(quote: quote) }
      } label: {
        label
      }
      .task(id: expanded) {
        if expanded { await model.loadTimelineEvidence(workspaceId: workspaceId, event: event) }
      }
    } else {
      label
    }
  }

  private var label: some View {
    HStack(alignment: .top) {
      Text(String(event.occurredAt.prefix(10))).font(.callout.monospaced()).frame(width: 100, alignment: .leading)
      Text(event.summary)
    }
  }
}

private struct DriverBlock: View {
  let driver: DetailDriver
  let quotes: [EvidenceItem]

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack {
        Text(driver.type.replacingOccurrences(of: "_", with: " ").capitalized).bold()
        Text(driver.severity).font(.caption).padding(.horizontal, 6).padding(.vertical, 1)
          .background(.quaternary, in: Capsule())
        if driver.basis == "metadata" { Text("from message counts and dates").font(.caption).foregroundStyle(.secondary) }
      }
      ForEach(driver.explanation, id: \.self) { line in
        Text(line).font(.caption).foregroundStyle(.secondary)
      }
      ForEach(quotes) { quote in QuoteView(quote: quote) }
    }
  }
}

private struct OutcomeSheet: View {
  let model: ClientDetailModel
  let workspaceId: String
  let clientId: String
  @Environment(\.dismiss) private var dismiss
  @State private var event = OutcomeEvent.renewed
  @State private var date = Date()

  var body: some View {
    Form {
      Text("Record what happened").font(.headline)
      Text("This teaches Firra which of its warnings and suggestions were right.")
        .font(.callout).foregroundStyle(.secondary)
      Picker("Outcome", selection: $event) {
        ForEach(OutcomeEvent.allCases, id: \.self) { Text($0.label).tag($0) }
      }
      DatePicker("Date", selection: $date, displayedComponents: .date)
      if let message = model.message, message != "Recorded." { Text(message).foregroundStyle(.red) }
      HStack {
        Button("Cancel") { dismiss() }
        Spacer()
        Button("Save") {
          Task {
            if await model.recordOutcome(workspaceId: workspaceId, clientId: clientId, event: event, on: date) {
              dismiss()
            }
          }
        }
        .keyboardShortcut(.defaultAction)
      }
    }
    .formStyle(.grouped).padding().frame(width: 420)
  }
}
