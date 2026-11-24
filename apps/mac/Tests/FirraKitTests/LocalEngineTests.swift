import Foundation
import Testing
@testable import FirraKit

@MainActor @Suite struct LocalEngineTests {
  let me = #"{"user":{"id":"owner","email":"owner@firra.local"},"memberships":[{"workspace_id":"workspace","workspace_name":"Local","role":"owner"}],"contributions":[]}"#

  @Test func readyBuildsALoopbackClientAndLoadsMeOncePerStart() async throws {
    let stub = StubTransport([.init(status: 200, body: me),
      .init(status: 200, body: #"{"generated_at":"2026-10-05","items":[]}"#),
      .init(status: 200, body: me)])
    let model = LocalEngineModel(transport: stub, restart: {})
    let ready = EngineState.running(EngineEndpoint(port: 49152, token: "first-token"))
    await model.receive(ready)
    await model.receive(ready)
    #expect(model.workspaceId == "workspace" && model.state == ready)
    let api = try #require(model.api)
    _ = try await api.attention(workspaceId: try #require(model.workspaceId))
    #expect(stub.requests.map { $0.url?.path } == ["/v1/me", "/v1/workspaces/workspace/attention"])
    #expect(stub.requests.allSatisfy { $0.url?.host == "127.0.0.1" && $0.url?.port == 49152 })
    #expect(stub.requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == "Bearer first-token" })
    await model.receive(.starting)
    await model.receive(.running(EngineEndpoint(port: 49153, token: "next-token")))
    #expect(stub.requests.last?.url?.port == 49153)
    #expect(stub.requests.last?.value(forHTTPHeaderField: "Authorization") == "Bearer next-token")
  }

  @Test func unauthorizedRestartsOnceAndClearsTheOldClient() async throws {
    let restarted = Flag()
    let stub = StubTransport([.init(status: 200, body: me),
      .init(status: 401, body: "{}"), .init(status: 401, body: "{}")])
    let model = LocalEngineModel(transport: stub, restart: { await restarted.set() })
    await model.receive(.running(EngineEndpoint(port: 49152, token: "synthetic-token")))
    let old = try #require(model.api)
    await #expect(throws: APIError.unauthenticated) { _ = try await old.attention(workspaceId: "workspace") }
    #expect(await restarted.value)
    #expect(model.state == .restarting(attempt: 1))
    #expect(model.api == nil && model.me == nil && model.workspaceId == nil)
    await #expect(throws: APIError.unauthenticated) { _ = try await old.me() }
    #expect(model.state == .restarting(attempt: 1))
  }

  @Test func aLateUnauthorizedAnswerCannotRestartAnEngineBeingDeleted() async throws {
    let restarted = Flag()
    let stub = StubTransport([.init(status: 200, body: me), .init(status: 401, body: "{}")])
    let model = LocalEngineModel(transport: stub, restart: { await restarted.set() })
    await model.receive(.running(EngineEndpoint(port: 49152, token: "synthetic")))
    let api = try #require(model.api)
    model.suspendForDeletion()
    await #expect(throws: APIError.unauthenticated) { _ = try await api.me() }
    #expect(!(await restarted.value) && model.api == nil && model.me == nil)
  }

  @Test func aLateOwnerAnswerCannotReviveAStoppedEngine() async {
    let transport = GatedTransport(reply: .init(status: 200, body: me))
    let model = LocalEngineModel(transport: transport, restart: {})
    let loading = Task { await model.receive(.running(EngineEndpoint(port: 49152, token: "old"))) }
    await transport.waitForRequests(1)
    await model.receive(.stopped)
    transport.releaseAll()
    await loading.value
    #expect(model.state == .stopped && model.api == nil && model.me == nil)
  }
}

@Suite struct EngineFailureTextTests {
  @Test @MainActor func failureIsKeptWhileTheSupervisorRetries() async {
    let model = LocalEngineModel(restart: {})
    await model.receive(.failed("launch failed: the engine exited right after it started"))
    await model.receive(.restarting(attempt: 1))
    #expect(model.lastFailure == "launch failed: the engine exited right after it started")
    await model.receive(.installing)
    #expect(model.lastFailure != nil)
  }
}
