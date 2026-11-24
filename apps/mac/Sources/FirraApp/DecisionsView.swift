import FirraKit
import SwiftUI

/// Suggestions grouped by server importance; evidence-backed cards wait for a loaded quote.
struct DecisionsView: View {
  let model: DecisionsModel
  let workspaceId: String

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text("Decisions").font(.title2.bold())
      Text("Short, ranked suggestions. Tell Firra whether each one helped so it gets better for your firm.")
        .foregroundStyle(.secondary)
      if let message = model.message { Text(message).foregroundStyle(.red) }
      switch model.phase {
      case .idle, .loading:
        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
      case .failed(let text):
        ContentUnavailableView {
          Label("Could not load", systemImage: "exclamationmark.triangle")
        } description: {
          Text(text)
        } actions: {
          Button("Try again") { Task { await model.load(workspaceId: workspaceId) } }
        }
      case .ready:
        List {
          ForEach(model.sections) { group in
            Section(group.id) {
              ForEach(group.cards) { card in
                if model.isVerified(card) {
                  CardRow(model: model, workspaceId: workspaceId, card: card)
                } else if model.quotesFailed.contains(card.id) {
                  HStack {
                    Label(
                      "A suggestion for \(card.clientName) is hidden: its quotes could not be loaded.",
                      systemImage: "exclamationmark.triangle"
                    )
                    .font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Retry") { Task { await model.loadEvidence(workspaceId: workspaceId, card: card) } }
                      .buttonStyle(.link)
                  }
                } else {
                  HStack {
                    ProgressView().controlSize(.small)
                    Text("Checking the quotes behind a suggestion for \(card.clientName)…")
                      .font(.callout).foregroundStyle(.secondary)
                  }
                }
              }
            }
          }
          if model.nextCursor != nil {
            HStack {
              Button("Load more") { Task { await model.loadMore(workspaceId: workspaceId) } }.buttonStyle(.link)
              if model.loadMoreFailed { Text("Could not load more. Try again.").font(.caption).foregroundStyle(.red) }
            }
          }
        }
        .overlay {
          if model.cards.isEmpty {
            ContentUnavailableView(
              "No new suggestions", systemImage: "checkmark.circle",
              description: Text("Weekly suggestions appear here."))
          }
        }
      }
    }
    .padding()
    .task(id: workspaceId) { await model.load(workspaceId: workspaceId) }
  }
}

private struct CardRow: View {
  @Environment(NotificationFocus.self) private var notificationFocus
  let model: DecisionsModel
  let workspaceId: String
  let card: DecisionItem

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack {
        Text(card.clientName).font(.caption.bold()).foregroundStyle(.secondary)
        Text(card.kind.capitalized).font(.caption).foregroundStyle(.secondary)
        Text("evidence \(card.strength)").font(.caption).foregroundStyle(.secondary)
      }
      Text(card.title).font(.headline)
      Text(card.body)
      ForEach(model.quotes[card.id] ?? []) { quote in QuoteView(quote: quote) }
      if let feedback = card.feedback {
        Text("You said: \(feedback.verdict.replacingOccurrences(of: "_", with: " "))")
          .font(.caption).foregroundStyle(.secondary)
      } else {
        HStack {
          ForEach(FeedbackVerdict.allCases, id: \.self) { verdict in
            Button(verdict.label) {
              Task { await model.rate(workspaceId: workspaceId, card: card, verdict: verdict) }
            }
          }
        }
      }
    }
    .onAppear { notificationFocus.decisionIDs.insert(card.id) }
    .onDisappear { notificationFocus.decisionIDs.remove(card.id) }
    .padding(.vertical, 4)
  }
}

/// One verified quote: the words (plain text), who and when, the original and the local copy.
struct QuoteView: View {
  let quote: EvidenceItem

  var body: some View {
    VStack(alignment: .leading, spacing: 2) {
      Text("“\(quote.quote)”").italic().textSelection(.enabled)
      HStack(spacing: 6) {
        Text(attribution).font(.caption).foregroundStyle(.secondary)
        if let link = quote.deepLink, let url = URL(string: link), url.scheme == "https" {
          Link("Open original", destination: url).font(.caption)
        }
      }
    }
    .padding(.leading, 10)
    .overlay(alignment: .leading) { Rectangle().fill(.tertiary).frame(width: 2) }
  }

  private var attribution: String {
    let date = ISO8601DateFormatter().date(from: quote.occurredAt)?.formatted(date: .abbreviated, time: .omitted)
      ?? quote.occurredAt
    return [quote.authorName, quote.authorRole, date].compactMap { $0 }.joined(separator: " · ")
  }
}

