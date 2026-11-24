import FirraKit
import SwiftUI

/// Replays of stored history: what Firra would have shown for each client, week by week, and how
/// early it would have warned before a client gave notice or left.
struct ReplayView: View {
  let model: ReplayModel
  let workspaceId: String
  @State private var requesting = false
  @State private var runId: String?
  @State private var clientId: String?

  var body: some View {
    HSplitView {
      VStack(alignment: .leading, spacing: 10) {
        HStack {
          Text("Replays").font(.title2.bold())
          Spacer()
          Button("New replay…") { requesting = true }
        }
        if let message = model.message { Text(message).font(.callout).foregroundStyle(.red) }
        List(model.runs, selection: $runId) { run in
          VStack(alignment: .leading, spacing: 2) {
            Text("\(run.windowStart) → \(run.windowEnd)")
            Text(Self.summary(run)).font(.caption).foregroundStyle(.secondary)
          }
          .tag(run.id)
        }
        .overlay {
          if model.runs.isEmpty {
            ContentUnavailableView(
              "No replays yet", systemImage: "clock.arrow.circlepath",
              description: Text("A replay walks through stored history and shows how early Firra would have seen trouble."))
          }
        }
        if runId != nil {
          Text("Clients").font(.headline)
          List(model.clients, selection: $clientId) { client in
            Text(client.name + (client.status == "churned" ? " (left)" : "")).tag(client.id)
          }
          .frame(minHeight: 120)
        }
      }
      .padding()
      .frame(minWidth: 260, idealWidth: 300)

      ScrollView {
        if let result = model.result { ReplayStory(result: result) } else {
          Text(runId == nil ? "Choose a replay." : "Choose a client.")
            .foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .center).padding(40)
        }
      }
      .frame(minWidth: 360)
    }
    .task(id: workspaceId) { await model.loadRuns(workspaceId: workspaceId) }
    .onChange(of: clientId) { _, client in
      guard let runId, let client else { return }
      Task { await model.open(workspaceId: workspaceId, runId: runId, clientId: client) }
    }
    .onChange(of: runId) { _, run in
      guard let run, let clientId else { return }
      Task { await model.open(workspaceId: workspaceId, runId: run, clientId: clientId) }
    }
    .sheet(isPresented: $requesting) { RequestReplaySheet(model: model, workspaceId: workspaceId) }
  }

  static func summary(_ run: ReplayRunItem) -> String {
    guard let totals = run.totals else { return run.status.capitalized }
    var parts = ["\(totals.lossesDetected30D) of \(totals.losses) losses seen 30+ days ahead"]
    if let lead = totals.medianLeadDays { parts.append("median \(Int(lead)) days early") }
    parts.append(
      String(format: "%.1f false-alarm weeks per 10 clients a month", totals.falseAlarmClientWeeksPer10ClientsPerMonth))
    return parts.joined(separator: " · ")
  }
}

/// One client's replay: the headline, a strip of weekly states and the changes with their plays.
private struct ReplayStory: View {
  let result: ReplayClientResult

  var body: some View {
    let story = result.narrative
    VStack(alignment: .leading, spacing: 14) {
      Text(headline(story)).font(.title3.bold())
      if !result.weeks.isEmpty {
        HStack(spacing: 2) {
          ForEach(Array(result.weeks.enumerated()), id: \.offset) { _, week in
            Rectangle().fill(color(week.risk)).frame(width: 8, height: 26)
              .help("\(week.asOf): \(week.risk)")
          }
        }
        .accessibilityLabel("Weekly risk states from \(result.weeks.first?.asOf ?? "") to \(result.weeks.last?.asOf ?? "")")
        Text("Each bar is one week: green stable, yellow watch, orange elevated, red high.")
          .font(.caption).foregroundStyle(.secondary)
      }
      Text("Changes").font(.headline)
      ForEach(Array(story.transitions.enumerated()), id: \.offset) { _, change in
        HStack(alignment: .top) {
          Text(change.asOf).font(.callout.monospaced()).frame(width: 100, alignment: .leading)
          VStack(alignment: .leading, spacing: 2) {
            Text("\(change.fromState ?? "new") → \(change.toState)")
            Text("Suggested: " + change.play.replacingOccurrences(of: "_", with: " "))
              .font(.caption).foregroundStyle(.secondary)
          }
          Spacer()
          if let days = change.daysBeforeEnd { Text("\(days) days before the end").font(.caption) }
        }
      }
      if story.transitions.isEmpty { Text("No change of state in this window.").foregroundStyle(.secondary) }
      if !story.caveats.isEmpty {
        Text("Caveats: " + story.caveats.map { $0.replacingOccurrences(of: "_", with: " ") }.joined(separator: ", "))
          .font(.caption).foregroundStyle(.secondary)
      }
    }
    .padding()
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private func headline(_ story: ReplayNarrative) -> String {
    if let end = story.endDate {
      let what = story.endKind == "notice" ? "gave notice" : "left"
      if let lead = story.leadDays, lead > 0 {
        return "This client \(what) on \(end); Firra would have warned \(lead) days earlier."
      }
      return "This client \(what) on \(end); Firra would not have warned in time."
    }
    if story.weeksElevated > 0 {
      return "Still a client; \(story.weeksElevated) weeks at elevated risk or higher in this window."
    }
    return "Still a client; no elevated risk in this window."
  }

  private func color(_ risk: String) -> Color {
    switch risk {
    case "high": .red
    case "elevated": .orange
    case "watch": .yellow
    default: .green
    }
  }
}

private struct RequestReplaySheet: View {
  let model: ReplayModel
  let workspaceId: String
  @Environment(\.dismiss) private var dismiss
  @State private var start = Calendar.current.date(byAdding: .month, value: -12, to: Date()) ?? Date()
  @State private var end = Date()
  @State private var stepDays = 7

  var body: some View {
    Form {
      Text("New replay").font(.headline)
      Text(
        "Firra walks through stored history and records how each client's risk and opportunity "
          + "changed. It uses stored facts only and adds model usage to your monthly budget."
      )
      .font(.callout).foregroundStyle(.secondary)
      DatePicker("From", selection: $start, displayedComponents: .date)
      DatePicker("To", selection: $end, displayedComponents: .date)
      Picker("Step", selection: $stepDays) {
        Text("Daily").tag(1)
        Text("Weekly").tag(7)
        Text("Every two weeks").tag(14)
      }
      switch model.state {
      case .failed(let text): Text(text).foregroundStyle(.red)
      case .requested: Label("Started. It appears in the list when it finishes.", systemImage: "checkmark.circle")
      default: EmptyView()
      }
      HStack {
        Button("Close") { dismiss() }
        Spacer()
        Button("Start replay") {
          Task { await model.request(workspaceId: workspaceId, start: start, end: end, stepDays: stepDays) }
        }
        .keyboardShortcut(.defaultAction)
        .disabled(model.state == .sending)
      }
    }
    .formStyle(.grouped).padding().frame(width: 460)
  }
}
