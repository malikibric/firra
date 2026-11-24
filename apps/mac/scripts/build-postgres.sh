#!/usr/bin/env bash
# Build on Apple Silicon with Command Line Tools; no Homebrew runtime libraries.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT="$ROOT/apps/mac/build-engine"
BUNDLE="${1:-$OUT/engine}"
mkdir -p "$BUNDLE" "$OUT/source"
BUNDLE="$(cd "$BUNDLE" && pwd)"
[[ "$(uname -m)" == arm64 && "$(uname -s)" == Darwin ]]
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export MACOSX_DEPLOYMENT_TARGET=14.0
START=$SECONDS
cd "$OUT/source"
curl -fL --retry 3 -o postgres.tar.bz2 https://ftp.postgresql.org/pub/source/v17.11/postgresql-17.11.tar.bz2
curl -fL --retry 3 -o postgres.sha256 https://ftp.postgresql.org/pub/source/v17.11/postgresql-17.11.tar.bz2.sha256
EXPECTED="$(awk '{print $1}' postgres.sha256)"
[[ "$(shasum -a 256 postgres.tar.bz2 | awk '{print $1}')" == "$EXPECTED" ]]
tar -xf postgres.tar.bz2
cd postgresql-17.11
./configure --prefix="$BUNDLE/postgres" --without-icu --without-readline \
  --without-zlib --without-lz4 --without-zstd --disable-rpath
make -j "$(sysctl -n hw.ncpu)"
make install
cd "$OUT/source"
curl -fL --retry 3 -o vector.tar.gz https://codeload.github.com/pgvector/pgvector/tar.gz/refs/tags/v0.8.2
tar -xf vector.tar.gz
cd pgvector-0.8.2
make PG_CONFIG="$BUNDLE/postgres/bin/pg_config" OPTFLAGS="" -j "$(sysctl -n hw.ncpu)"
make PG_CONFIG="$BUNDLE/postgres/bin/pg_config" install
cd "$BUNDLE/postgres"
for binary in bin/*; do
  case "${binary##*/}" in initdb|pg_ctl|postgres|psql|pg_dump|pg_restore) ;; *) rm "$binary" ;; esac
done
rm -rf include share/doc share/man share/locale lib/pgxs
find . -name '*.a' -delete
# Replace build-prefix load commands before signing. PostgreSQL's relative layout stays intact.
while IFS= read -r -d '' binary; do
  file -b "$binary" | grep -q 'Mach-O' || continue
  while IFS= read -r dependency; do
    case "$dependency" in
      "$BUNDLE/postgres/"*)
        relative="${dependency#"$BUNDLE/postgres/"}"
        case "$binary" in ./bin/*) target="@loader_path/../$relative" ;; *) target="@loader_path/${dependency##*/}" ;; esac
        install_name_tool -change "$dependency" "$target" "$binary" ;;
      /usr/lib/*|/System/Library/*|@loader_path/*|@rpath/*) ;;
      *) echo "Unexpected library: $dependency" >&2; exit 1 ;;
    esac
  done < <(otool -L "$binary" | tail -n +2 | sed 's/^[[:space:]]*//; s/ (compatibility.*//')
  case "$binary" in *.dylib) install_name_tool -id "@loader_path/${binary##*/}" "$binary" ;; esac
  codesign --force -s - "$binary"
done < <(find ./lib ./bin -depth -type f -print0)
echo "postgres_build_seconds=$((SECONDS - START))"
du -sk "$BUNDLE/postgres"
