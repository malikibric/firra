import Foundation
import Testing

@testable import FirraKit

private let hash = String(repeating: "a", count: 64)
private func feedJSON(_ version: String, url: String = "https://releases.example/Firra.dmg", sha: String = hash) -> Data {
  Data(#"{"version":"\#(version)","url":"\#(url)","sha256":"\#(sha)","notes":"n"}"#.utf8)
}

@Suite struct UpdatesTests {
  @Test func versionOrder() {
    #expect(VersionOrder.isNewer("1.2.10", than: "1.2.9"))
    #expect(VersionOrder.isNewer("1.0.0", than: "1.0.0-pilot"))
    #expect(!VersionOrder.isNewer("1.0.0-pilot", than: "1.0.0"))
    #expect(!VersionOrder.isNewer("0.1.0", than: "0.1.0"))
    #expect(!VersionOrder.isNewer("0.9.0", than: "0.10.0"))
    #expect(!VersionOrder.isNewer("garbage", than: "0.1.0"))
    #expect(!VersionOrder.isNewer("1.0", than: "0.1.0"))
  }

  @Test @MainActor func reportsNewerValidRelease() async {
    let model = UpdateModel(feed: URL(string: "https://releases.example/latest.json"), currentVersion: "0.1.0") { _ in feedJSON("0.2.0") }
    await model.checkIfDue()
    #expect(model.available?.version == "0.2.0")
    #expect(model.verifyHint?.contains(hash) == true)
  }

  @Test @MainActor func sameOrOlderVersionIsNotAnUpdate() async {
    let model = UpdateModel(feed: URL(string: "https://releases.example/latest.json"), currentVersion: "0.2.0") { _ in feedJSON("0.2.0") }
    await model.checkIfDue()
    #expect(model.available == nil && model.error == nil)
  }

  @Test @MainActor func refusesInvalidEntries() async {
    for bad in [feedJSON("0.2.0", url: "http://releases.example/Firra.dmg"), feedJSON("0.2.0", sha: "xyz"), feedJSON("latest"), Data("{}".utf8), Data(count: UpdateModel.maxFeedBytes + 1)] {
      let model = UpdateModel(feed: URL(string: "https://releases.example/latest.json"), currentVersion: "0.1.0") { _ in bad }
      await model.checkIfDue()
      #expect(model.available == nil && model.error != nil)
    }
  }

  @Test @MainActor func isOffWithoutAnHTTPSFeedAndChecksOncePerDay() async {
    let calls = Counter()
    let off = UpdateModel(feed: URL(string: "http://releases.example/latest.json"), currentVersion: "0.1.0") { _ in await calls.bump(); return feedJSON("0.2.0") }
    await off.checkIfDue()
    #expect(!off.enabled && off.available == nil)
    #expect(await calls.value == 0)

    let clock = Clock(Date(timeIntervalSince1970: 1_000_000))
    let model = UpdateModel(feed: URL(string: "https://releases.example/latest.json"), currentVersion: "0.1.0", fetch: { _ in await calls.bump(); return feedJSON("0.2.0") }, now: { clock.date })
    await model.checkIfDue(); await model.checkIfDue()
    #expect(await calls.value == 1)
    clock.date = clock.date.addingTimeInterval(25 * 3600)
    await model.checkIfDue()
    #expect(await calls.value == 2)
  }

  @Test @MainActor func networkFailureIsASafeMessage() async {
    struct Boom: Error {}
    let model = UpdateModel(feed: URL(string: "https://releases.example/latest.json"), currentVersion: "0.1.0") { _ in throw Boom() }
    await model.checkIfDue()
    #expect(model.error == "Could not check for updates.")
  }
}

private actor Counter { var value = 0; func bump() { value += 1 } }
private final class Clock: @unchecked Sendable { var date: Date; init(_ d: Date) { date = d } }
