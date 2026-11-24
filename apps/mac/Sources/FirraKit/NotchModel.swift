import Foundation
import Observation

@MainActor @Observable public final class NotchModel {
  public private(set) var state = NotchState()
  private let attention: AttentionModel
  private let meetings: any MeetingBriefProviding
  private let now: @Sendable () -> Date
  private let sleep: @Sendable (Duration) async throws -> Void
  private var task: Task<Void, Never>?
  private var generation = 0
  private var currentWorkspace: String?

  public init(
    attention: AttentionModel, meetings: any MeetingBriefProviding = NoMeetingBriefs(),
    now: @escaping @Sendable () -> Date = { Date() },
    sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
  ) {
    self.attention = attention
    self.meetings = meetings
    self.now = now
    self.sleep = sleep
  }

  public func send(_ event: NotchEvent) { state.apply(event) }

  public func start(workspaceId: @escaping @MainActor () -> String?) {
    stop()
    let started = generation
    task = Task { [weak self, sleep] in
      while !Task.isCancelled {
        guard let self else { return }
        await self.refresh(workspaceId: workspaceId, generation: started)
        guard !Task.isCancelled, self.generation == started else { return }
        do { try await sleep(.seconds(30)) } catch { return }
      }
    }
  }

  public func stop() {
    generation += 1
    task?.cancel()
    task = nil
    currentWorkspace = nil
    state = NotchState()
  }

  private func refresh(workspaceId: @MainActor () -> String?, generation started: Int) async {
    let workspace = workspaceId()
    if currentWorkspace != workspace {
      state = NotchState()
      currentWorkspace = workspace
    }
    // Attention can still belong to the previous workspace while its replacement is loading.
    state.apply(.items(workspace != nil && attention.workspaceId == workspace ? attention.items : []))
    let upcoming: [MeetingBriefSummary]
    if let workspace { upcoming = await meetings.upcoming(workspaceId: workspace) }
    else { upcoming = [] }
    // A provider may ignore cancellation. Its late reply must not restore a stopped session.
    guard generation == started, !Task.isCancelled else { return }
    guard workspaceId() == workspace else {
      state = NotchState()
      currentWorkspace = nil
      return
    }
    state.apply(.meetings(upcoming))
    state.apply(.tick(now()))
  }
}
