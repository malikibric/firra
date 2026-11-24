// swift-tools-version: 6.0
import PackageDescription

let package = Package(
  name: "Firra",
  platforms: [.macOS(.v14)],
  products: [
    .library(name: "FirraKit", targets: ["FirraKit"]),
    .executable(name: "Firra", targets: ["FirraApp"]),
  ],
  targets: [
    // Everything testable: local API client, Google OAuth, engine supervision and view models.
    .target(name: "FirraKit", resources: [.process("Resources")]),
    // The SwiftUI shell: menu bar item and local engine screens.
    .executableTarget(name: "FirraApp", dependencies: ["FirraKit"]),
    .testTarget(name: "FirraKitTests", dependencies: ["FirraKit"], resources: [.copy("Fixtures")]),
  ]
)
