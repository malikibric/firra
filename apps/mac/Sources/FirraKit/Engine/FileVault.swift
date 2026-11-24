import Foundation

public enum FileVaultState: String, Sendable {
  case on, off, unknown
  static func parse(_ output: String) -> Self {
    if output.contains("FileVault is On") { return .on }
    if output.contains("FileVault is Off") { return .off }
    return .unknown
  }
}

extension EngineSupervisor {
  /// Native read-only check; no shell, credentials or customer data.
  public static func fileVaultStatus() async -> FileVaultState {
    await Task.detached {
      let process = Process()
      process.executableURL = URL(fileURLWithPath: "/usr/bin/fdesetup")
      process.arguments = ["status"]
      let pipe = Pipe()
      process.standardOutput = pipe
      process.standardError = FileHandle.nullDevice
      do {
        try process.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { return FileVaultState.unknown }
        return FileVaultState.parse(String(decoding: data, as: UTF8.self))
      } catch { return FileVaultState.unknown }
    }.value
  }
}
