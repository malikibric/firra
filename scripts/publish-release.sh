#!/usr/bin/env bash
# Writes latest.json for a built DMG and, with --yes, uploads both to the public release bucket (plan L6-02).
#   scripts/publish-release.sh apps/mac/build/Firra-0.1.0.dmg 0.1.0 "short release notes"            # dry run: prints latest.json
#   FIRRA_RELEASE_BUCKET=gs://<bucket> scripts/publish-release.sh <dmg> <version> "<notes>" --yes      # uploads (owner's go)
# The app only reports a new version and shows the SHA-256; it never downloads or installs anything.
set -euo pipefail
dmg="${1:?dmg path}"; version="${2:?version, e.g. 0.1.0}"; notes="${3:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || { echo "bad version"; exit 2; }
[[ -f "$dmg" ]] || { echo "no such file"; exit 2; }
sha="$(shasum -a 256 "$dmg" | cut -d' ' -f1)"
name="Firra-$version.dmg"
bucket="${FIRRA_RELEASE_BUCKET:-}"
base="${FIRRA_RELEASE_BASE_URL:-https://storage.googleapis.com/${bucket#gs://}}"
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
python3 - "$version" "$base/$name" "$sha" "$notes" >"$out/latest.json" <<'PY'
import json, sys
version, url, sha, notes = sys.argv[1:5]
print(json.dumps({"version": version, "url": url, "sha256": sha, "notes": notes[:300]}, indent=2))
PY
cat "$out/latest.json"
[[ "${4:-}" == "--yes" ]] || { echo "Dry run. Add --yes (and FIRRA_RELEASE_BUCKET) after the owner's go."; exit 0; }
: "${FIRRA_RELEASE_BUCKET:?set FIRRA_RELEASE_BUCKET=gs://...}"
gcloud storage cp "$dmg" "$FIRRA_RELEASE_BUCKET/$name" --cache-control="public, max-age=31536000"
gcloud storage cp "$out/latest.json" "$FIRRA_RELEASE_BUCKET/latest.json" --cache-control="no-cache" --content-type=application/json
echo "Published $name sha256=$sha"
