#if DEBUG
  import AppKit
  import FirraKit
  import Foundation

  /// `swift run Firra --demo-notch`: the pill with made-up clients and a made-up meeting, no engine and
  /// no network. Debug builds only; nothing here is compiled into a release.
  @MainActor final class NotchDemo {
    private struct DemoTransport: HTTPTransport {
      func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let body = """
          {"generated_at":"2026-10-03T09:00:00.000Z","items":[
           {"client_id":"c1","name":"Kestrel Labs (demo)","status":"active","policy":"full","as_of":"2026-10-02",
            "risk_state":"high","previous_risk_state":"elevated","state_since":"2026-09-25","expansion_state":"none",
            "strength":"moderate","play":"fix_first","days_to_notice":24,"drivers":[]},
           {"client_id":"c2","name":"Vantage Co (demo)","status":"active","policy":"full","as_of":"2026-10-02",
            "risk_state":"elevated","previous_risk_state":"watch","state_since":"2026-09-28","expansion_state":"none",
            "strength":"weak","play":"renewal_prep","days_to_notice":null,"drivers":[]}]}
          """
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        return (Data(body.utf8), response)
      }
    }

    private let model: NotchModel
    private let controller: NotchController

    static var requested: Bool { CommandLine.arguments.contains("--demo-notch") }

    init() {
      let api = APIClient(
        base: URL(string: "https://demo.invalid")!, transport: DemoTransport(), tokenProvider: { "demo" })
      let attention = AttentionModel(api: api)
      model = NotchModel(attention: attention, meetings: PreviewMeetingBriefs())
      controller = NotchController(model: model)
      Task { @MainActor [model, controller] in
        await attention.load(workspaceId: "demo")
        model.start { "demo" }
        controller.start()
      }
    }
  }
#endif
