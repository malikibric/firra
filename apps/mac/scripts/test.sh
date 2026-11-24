#!/usr/bin/env bash
# Runs the Swift tests with Xcode when it is installed, else with the Command Line Tools' Swift Testing
# framework (their cross-import overlay is missing, so it is switched off).
set -euo pipefail
cd "$(dirname "$0")/.."
if xcodebuild -version >/dev/null 2>&1; then
  exec swift test "$@"
fi
FW=/Library/Developer/CommandLineTools/Library/Developer/Frameworks
exec swift test \
  -Xswiftc -F"$FW" -Xswiftc -Xfrontend -Xswiftc -disable-cross-import-overlays \
  -Xlinker -F"$FW" -Xlinker -rpath -Xlinker "$FW" "$@"
