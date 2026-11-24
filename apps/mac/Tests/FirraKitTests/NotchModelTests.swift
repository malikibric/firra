import Foundation
import Testing
@testable import FirraKit

/// Manual suspension points: no wall-clock waits, including when cancellation is tested.
private actor NotchGate {
  private var count = 0
  private var pending: [CheckedContinuation<Void, Never>] = []
  private var observers: [(Int, CheckedContinuation<Void, Never>)] = []
  private(set) var durations: [Duration] = []
  func pause(_ duration: Duration = .zero) async {
    durations.append(duration)
    await withCheckedContinuation { continuation in
      pending.append(continuation)
      count += 1
      let ready = observers.filter { $0.0 <= count }
      observers.removeAll { $0.0 <= count }
      for (_, waiter) in ready { waiter.resume() }
    }
  }
  func waitFor(_ target: Int) async {
    if count >= target { return }
    await withCheckedContinuation { observers.append((target, $0)) }
  }
  func release() { if !pending.isEmpty { pending.removeFirst().resume() } }
}

private actor FakeNotchMeetings: MeetingBriefProviding {
  var result: [MeetingBriefSummary]
  private(set) var requests: [String] = []
  let gate: NotchGate?
  init(_ result: [MeetingBriefSummary] = [], gate: NotchGate? = nil) {
    self.result = result; self.gate = gate
  }
  func upcoming(workspaceId: String) async -> [MeetingBriefSummary] {
    requests.append(workspaceId)
    if let gate { await gate.pause() }
    return result
  }
}

@MainActor private final class NotchWorkspace { var id: String? = "one" }

@MainActor @Suite struct NotchModelTests {
  func attention(_ items: [AttentionItem]) throws -> AttentionModel {
    let data = try JSONEncoder.api.encode(AttentionResponse(generatedAt: "fixture", items: items))
    let api = APIClient(base: URL(string: "https://api.example")!,
      transport: StubTransport([.init(status: 200, body: String(decoding: data, as: UTF8.self))]),
      tokenProvider: { "fixture" })
    return AttentionModel(api: api)
  }

  @Test func startsImmediatelyTicksEveryThirtySecondsAndForwardsEvents() async throws {
    let attention = try attention([notchItem("c", "high")])
    await attention.load(workspaceId: "w")
    let sleeper = NotchGate(), provider = FakeNotchMeetings([notchMeeting()])
    let model = NotchModel(attention: attention, meetings: provider, now: { notchNow },
      sleep: { await sleeper.pause($0) })
    model.start { "w" }
    await sleeper.waitFor(1)
    #expect(model.state.level == .high && model.state.mode == .meeting(notchMeeting()))
    model.send(.dismiss); model.send(.hover(true)); #expect(model.state.mode == .hint)
    await sleeper.release(); await sleeper.waitFor(2)
    #expect(model.state.mode == .hint)
    #expect(await provider.requests == ["w", "w"])
    #expect(await sleeper.durations == [.seconds(30), .seconds(30)])
    attention.forget(clientId: "c")
    await sleeper.release(); await sleeper.waitFor(3)
    #expect(model.state.level == .calm && model.state.topItems.isEmpty && model.state.mode == .dot)
    model.stop(); #expect(model.state == NotchState())
    await sleeper.release()
  }

  @Test func workspaceSwitchClearsOldItemsAndNoWorkspaceClearsMeetings() async throws {
    let attention = try attention([notchItem("old", "high")])
    await attention.load(workspaceId: "one")
    let sleeper = NotchGate(), provider = FakeNotchMeetings([notchMeeting()])
    let model = NotchModel(attention: attention, meetings: provider, now: { notchNow },
      sleep: { await sleeper.pause($0) })
    let workspace = NotchWorkspace()
    model.start { workspace.id }; await sleeper.waitFor(1)
    #expect(model.state.level == .high)
    workspace.id = "two"
    await sleeper.release(); await sleeper.waitFor(2)
    #expect(model.state.level == .calm && model.state.topItems.isEmpty)
    #expect(await provider.requests == ["one", "two"])
    workspace.id = nil
    await sleeper.release(); await sleeper.waitFor(3)
    #expect(model.state == tickedEmptyState())
    #expect(await provider.requests == ["one", "two"])
    model.stop(); await sleeper.release()
  }

  @Test func stopAndRestartDiscardAnUncancellableProviderReply() async throws {
    let attention = try attention([])
    let providerGate = NotchGate(), sleeper = NotchGate()
    let model = NotchModel(attention: attention, meetings: FakeNotchMeetings([notchMeeting()], gate: providerGate),
      now: { notchNow }, sleep: { await sleeper.pause($0) })
    model.start { "old" }; await providerGate.waitFor(1)
    model.stop(); #expect(model.state == NotchState())
    model.start { nil }; await sleeper.waitFor(1)
    await providerGate.release()
    for _ in 0..<10 { await Task.yield() }
    #expect(model.state == tickedEmptyState())
    model.stop(); await sleeper.release()
  }

  @Test func defaultProviderIsEmpty() async {
    #expect(await NoMeetingBriefs().upcoming(workspaceId: "w") == [])
  }

  private func tickedEmptyState() -> NotchState {
    var state = NotchState(); state.apply(.tick(notchNow)); return state
  }
}
