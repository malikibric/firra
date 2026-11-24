import Foundation
import Testing

@testable import FirraKit

private final class FakeLogin: @unchecked Sendable {
  var current: LoginItemStatus
  var registerFails = false
  var afterRegister: LoginItemStatus = .enabled
  init(_ s: LoginItemStatus) { current = s }
  struct Refused: Error {}
  var actions: LoginItemActions {
    LoginItemActions(
      status: { self.current },
      register: { if self.registerFails { throw Refused() }; self.current = self.afterRegister },
      unregister: { self.current = .disabled })
  }
}

@MainActor @Suite struct LoginItemTests {
  @Test func turnsOnAndOff() {
    let fake = FakeLogin(.disabled)
    let model = LoginItemModel(actions: fake.actions)
    #expect(!model.isOn)
    model.set(true); #expect(model.isOn && model.message == nil)
    model.set(false); #expect(!model.isOn)
  }

  @Test func refusalExplainsTheManualRoute() {
    let fake = FakeLogin(.disabled); fake.registerFails = true
    let model = LoginItemModel(actions: fake.actions)
    model.set(true)
    #expect(!model.isOn && model.message == LoginItemModel.manualHint)
  }

  @Test func approvalNeededIsShownAsOnWithAHint() {
    let fake = FakeLogin(.disabled); fake.afterRegister = .needsApproval
    let model = LoginItemModel(actions: fake.actions)
    model.set(true)
    #expect(model.isOn && model.message?.contains("Approve") == true)
  }

  @Test func silentNoOpIsNotReportedAsSuccess() {
    let fake = FakeLogin(.disabled); fake.afterRegister = .unavailable
    let model = LoginItemModel(actions: fake.actions)
    model.set(true)
    #expect(!model.isOn && model.message == LoginItemModel.manualHint)
  }
}
