#!/usr/bin/env bash
# Builds Firra.app (Swift release build + bundled engine) and a DMG, all on this Mac (plan L6-01).
# No Apple Developer account: the app is ad-hoc signed (or with the local identity named in
# FIRRA_SIGN_IDENTITY, see README) and opened the first time with right-click -> Open. The app copies
# its engine into Application Support at first start and clears quarantine on the copy (R1 fallback,
# docs/research/engine-bundle.md).
#
#   apps/mac/scripts/build-app.sh [version]            # builds the engine bundle first if missing
#   FIRRA_ENGINE_BUNDLE=/path/to/engine apps/mac/scripts/build-app.sh 0.1.0
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-0.1.0}"
[[ "$(uname -m)" == arm64 && "$(uname -s)" == Darwin ]] || { echo "Apple Silicon Mac required" >&2; exit 1; }
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
# CFBundleVersion keys the installed engine copy: every build gets its own, so an upgrade replaces it.
BUILD_ID="$VERSION+$SHA.$(date +%Y%m%d%H%M%S)"
ENGINE="${FIRRA_ENGINE_BUNDLE:-build-engine/engine}"
OUT="build"
APP="$OUT/Firra.app"
DMG="$OUT/Firra-$VERSION.dmg"

if [[ ! -x "$ENGINE/python/bin/python3.12" || ! -x "$ENGINE/postgres/bin/postgres" ]]; then
  echo "== engine bundle missing: building it (PostgreSQL + pgvector from source, Python wheels)"
  rm -rf "$ENGINE"
  scripts/build-postgres.sh "$ENGINE"
  scripts/build-python.sh "$ENGINE"
fi

# The engine's own code and data files are refreshed on every build (the heavy parts, Postgres and the
# Python runtime with third-party wheels, are reused): wheels from this checkout, then resources/.
echo "== refreshing the engine code and resources"
ROOT="$(cd ../.. && pwd)"
PY="$ENGINE/python/bin/python3.12"
SITE="$ENGINE/python/lib/python3.12/site-packages"
rm -rf build-engine/wheels && mkdir -p build-engine/wheels
( cd "$ROOT" && uv build --wheel --package firra-contracts --out-dir apps/mac/build-engine/wheels \
    && uv build --wheel --package firra-worker --out-dir apps/mac/build-engine/wheels )
rm -rf "$SITE"/firra "$SITE"/firra_contracts "$SITE"/firra_worker-* "$SITE"/firra_contracts-*
uv pip install --python "$PY" --target "$SITE" --no-deps --only-binary :all: build-engine/wheels/*.whl
find "$SITE/firra" "$SITE/firra_contracts" -name __pycache__ -prune -exec rm -rf {} +
RES="$ENGINE/resources"
rm -rf "$RES" && mkdir -p "$RES/supabase" "$RES/contracts"
cp -R "$ROOT/services/worker/config" "$RES/config"
cp -R "$ROOT/services/worker/prompts" "$RES/prompts"
cp -R "$ROOT/supabase/migrations" "$RES/supabase/migrations"
cp -R "$ROOT/supabase/local" "$RES/supabase/local"
cp -R "$ROOT/packages/contracts/schemas/local" "$RES/contracts/local"
"$PY" -I -B -c 'import firra.local.engine, firra.local.mcp.server, firra_contracts; from firra import resources as r; \
  assert (r.config_dir()/"signals.yaml").is_file() and (r.supabase_dir()/"migrations").is_dir() \
  and (r.local_schemas_dir()/"ai_extraction_output.json").is_file() and r.prompts_dir().is_dir()' \
  || { echo "bundled engine cannot import or find its resources" >&2; exit 1; }

echo "== swift release build"
swift build -c release --product Firra
BIN="$(swift build -c release --show-bin-path)/Firra"

rm -rf "$APP" "$DMG"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Firra"
cp -R "$(dirname "$BIN")/Firra_FirraKit.bundle" "$APP/Contents/Resources/"
cp -R "$ENGINE" "$APP/Contents/Resources/engine"

# Optional release address (https only): FIRRA_UPDATE_FEED=https://<bucket>/latest.json
UPDATE_FEED_ENTRY=""
if [[ -n "${FIRRA_UPDATE_FEED:-}" ]]; then
  [[ "$FIRRA_UPDATE_FEED" == https://* ]] || { echo "FIRRA_UPDATE_FEED must be https"; exit 2; }
  UPDATE_FEED_ENTRY="  <key>FirraUpdateFeed</key><string>$FIRRA_UPDATE_FEED</string>"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>app.firra.mac</string>
  <key>CFBundleName</key><string>Firra</string>
  <key>CFBundleDisplayName</key><string>Firra</string>
  <key>CFBundleExecutable</key><string>Firra</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD_ID</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
$UPDATE_FEED_ENTRY
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.business</string>
</dict></plist>
PLIST

echo "== signing (inside out)"
IDENTITY="${FIRRA_SIGN_IDENTITY:-Firra Local Signing}"
if security find-identity -p codesigning 2>/dev/null | grep -q "\"$IDENTITY\""; then SIGN="$IDENTITY"; else SIGN="-"; fi
# Every Mach-O in the bundled engine first (their own signatures are re-made under the app's identity),
# then the app. --deep is not used: it signs in an order that breaks nested code.
while IFS= read -r -d '' file; do
  file -b "$file" | grep -q 'Mach-O' || continue
  codesign --force --sign "$SIGN" "$file"
done < <(find "$APP/Contents/Resources/engine" -type f -print0)
codesign --force --sign "$SIGN" --identifier app.firra.mac "$APP"
codesign --verify --strict "$APP" && echo "signature ok ($([[ $SIGN == - ]] && echo ad-hoc || echo "$SIGN"))"

hdiutil create -volname "Firra" -srcfolder "$APP" -ov -format UDZO "$DMG" >/dev/null
( cd "$OUT" && shasum -a 256 "Firra-$VERSION.dmg" > "Firra-$VERSION.dmg.sha256" )
du -sh "$APP" "$DMG" | sed 's/^/size /'
echo "built $DMG ($(cut -d' ' -f1 "$OUT/Firra-$VERSION.dmg.sha256"))"
