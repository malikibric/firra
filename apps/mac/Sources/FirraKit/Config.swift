import Foundation

public struct FirraConfig: Sendable, Equatable {
  public let home: URL
  public let bundledEngine: URL
  public let buildID: String

  public init(home: URL, bundledEngine: URL, buildID: String) {
    self.home = home
    self.bundledEngine = bundledEngine
    self.buildID = buildID
  }

  public static func load(bundle: Bundle = .main) -> FirraConfig {
    FirraConfig(
      home: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appending(path: "Firra", directoryHint: .isDirectory),
      bundledEngine: bundle.bundleURL.appending(path: "Contents/Resources/engine"),
      buildID: bundle.infoDictionary?["CFBundleVersion"] as? String ?? "dev")
  }
}
