#!/usr/bin/env bash
# Only local project wheels are built. Third-party runtime dependencies must have wheels.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT="$ROOT/apps/mac/build-engine"
BUNDLE="${1:-$OUT/engine}"
[[ "$(uname -m)" == arm64 && "$(uname -s)" == Darwin ]]
mkdir -p "$BUNDLE" "$OUT/wheels"
BUNDLE="$(cd "$BUNDLE" && pwd)"
START=$SECONDS
cd "$ROOT"
uv python install 3.12.14 --install-dir "$OUT/python-download" --no-bin
SOURCE="$OUT/python-download/cpython-3.12.14-macos-aarch64-none"
[[ -d "$SOURCE" ]]
[[ ! -e "$BUNDLE/python" ]] || { echo 'Python output already exists; choose a fresh bundle.' >&2; exit 1; }
cp -R "$SOURCE" "$BUNDLE/python"
PYTHON="$BUNDLE/python/bin/python3.12"
uv export --frozen --package firra-worker --no-dev --no-emit-workspace -o "$OUT/runtime.txt"
uv build --wheel --package firra-contracts --out-dir "$OUT/wheels"
uv build --wheel --package firra-worker --out-dir "$OUT/wheels"
TARGET="$BUNDLE/python/lib/python3.12/site-packages"
uv pip install --python "$PYTHON" --target "$TARGET" --only-binary :all: \
  --require-hashes -r "$OUT/runtime.txt"
uv pip install --python "$PYTHON" --target "$TARGET" --no-deps --only-binary :all: "$OUT"/wheels/*.whl
rm -rf "$BUNDLE/python/include" "$BUNDLE/python/share" "$BUNDLE/python/lib/python3.12/test"
rm -rf "$TARGET/bin"
find "$BUNDLE/python/bin" -mindepth 1 ! -name python3.12 ! -name python3 ! -name python -delete
find "$BUNDLE/python" -type d \( -name __pycache__ -o -name tests \) -prune -exec rm -rf {} +
find "$BUNDLE/python" -name '*.a' -delete
while IFS= read -r -d '' binary; do
  file -b "$binary" | grep -q 'Mach-O' || continue
  case "$(lipo -archs "$binary")" in
    arm64) ;;
    *arm64*) lipo "$binary" -thin arm64 -output "$binary.arm64"; mv "$binary.arm64" "$binary" ;;
    *) echo "Missing arm64 slice: $binary" >&2; exit 1 ;;
  esac
  case "$binary" in *.dylib) install_name_tool -id "@loader_path/${binary##*/}" "$binary" ;; esac
  while IFS= read -r dependency; do
      case "$dependency" in
        "$SOURCE/"*|"$BUNDLE/python/"*)
          relative="${dependency#"$SOURCE/"}"
          relative="${relative#"$BUNDLE/python/"}"
          target="$("$PYTHON" -I -B -c 'import os, sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$BUNDLE/python/$relative" "$(dirname "$binary")")"
          install_name_tool -change "$dependency" "@loader_path/$target" "$binary" ;;
        /usr/lib/*|/System/Library/*|@loader_path/*|@rpath/*) ;;
        *) echo "Unexpected library: $dependency" >&2; exit 1 ;; esac
  done < <(otool -L "$binary" | tail -n +2 | sed 's/^[[:space:]]*//; s/ (compatibility.*//')
  codesign --force -s - "$binary"
done < <(find "$BUNDLE/python/lib" "$BUNDLE/python/bin" -depth -type f -print0)
"$PYTHON" -I -B -c 'import firra, firra_contracts, psycopg, fastapi, cryptography, uvloop, httptools, firra.local.engine, firra.local.mcp.server; print("runtime_imports=ok")'
echo "python_build_seconds=$((SECONDS - START))"
du -sk "$BUNDLE/python"
