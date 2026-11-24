# S2 — PostgreSQL and Python engine bundle

Tested 2026-10-05 on an Apple Silicon Mac running **macOS 26.5.2 (25F84)**, with
Command Line Tools and uv 0.12.18. Synthetic SQL only; no cloud, customer data, paid
calls, sudo, SIP changes, or Gatekeeper changes. Checkout: `~/firra-checkout`.

## Result and choice

**BLOCKED: direct quarantined launch was not repeatable after re-signing.** Initial
smokes passed, including recursive quarantine, but the final re-sign caused launch
assessment stalls. A copied bundle with quarantine cleared passes without admin
rights (R1 fallback). The lead must accept that fallback and update the direct
quarantined-smoke criterion before this spike can be marked done. Gatekeeper
assessment remains **rejected** for the ad-hoc executable (details below).

Choose **source-built PostgreSQL 17.11 + pgvector 0.8.2** and uv's
**python-build-standalone CPython 3.12.14**. The source build preserves PostgreSQL's
standard relative `bin/lib/share` layout, needs no source patches, and lets us omit
external ICU, readline, compression and TLS libraries. PostgreSQL connects only
over a private unix socket, so database TLS is unnecessary here. Python HTTPS uses
its bundled SSL libraries. PostgreSQL compression formats requiring zlib/lz4/zstd
are unavailable in this minimal spike; plain dumps remain possible.

| Option evaluated | Decision |
|---|---|
| PostgreSQL source + PGXS pgvector | Chosen: known compiler flags, native arm64, direct control over libraries and installed files |
| Postgres.app binaries | Viable alternative, not downloaded or benchmarked; still needs matching pgvector/PGXS headers and a library audit |
| zonky embedded binaries | Documented compact binaries intended for tests; not downloaded or benchmarked; matching development files and extension compatibility would still need checking |

Sources checked: [PostgreSQL build/relocation documentation](https://www.postgresql.org/docs/17/install-make.html),
[pgvector build instructions](https://github.com/pgvector/pgvector),
[zonky distribution documentation](https://github.com/zonkyio/embedded-postgres-binaries),
[uv managed Python](https://docs.astral.sh/uv/guides/install-python/), and
[python-build-standalone distributions](https://gregoryszorc.com/docs/python-build-standalone/main/distributions.html).
The Postgres.app documentation URL was inaccessible through the research browser;
no unsupported claims about its current binary contents are used here.

## Reproduction

Run from the clone root. Build tools are needed on the builder, not on the receiving Mac.
Build outputs, logs, downloaded sources, wheels and DMG are ignored under `build-engine/`.

```bash
mkdir -p apps/mac/build-engine
apps/mac/scripts/build-postgres.sh > apps/mac/build-engine/postgres-build.log 2>&1
apps/mac/scripts/build-python.sh > apps/mac/build-engine/python-build.log 2>&1
mv apps/mac/build-engine/engine 'apps/mac/build-engine/Relocated Engine'
mv apps/mac/build-engine/python-download apps/mac/build-engine/python-download-hidden
apps/mac/scripts/smoke-engine.sh 'apps/mac/build-engine/Relocated Engine'
xattr -w com.apple.quarantine '0081;00000000;Safari;' 'apps/mac/build-engine/Relocated Engine'
apps/mac/scripts/smoke-engine.sh 'apps/mac/build-engine/Relocated Engine'
xattr -wr com.apple.quarantine '0081;00000000;Safari;' 'apps/mac/build-engine/Relocated Engine'
apps/mac/scripts/smoke-engine.sh 'apps/mac/build-engine/Relocated Engine'
spctl --assess --type execute --verbose=4 'apps/mac/build-engine/Relocated Engine/postgres/bin/postgres'
hdiutil create -volname 'Firra Engine Spike' -srcfolder 'apps/mac/build-engine/Relocated Engine' \
  -ov -format UDZO apps/mac/build-engine/engine.dmg
du -sk 'apps/mac/build-engine/Relocated Engine'
stat -f %z apps/mac/build-engine/engine.dmg
```

Create `apps/mac/build-engine` before redirecting on a fresh checkout. PostgreSQL
17.11 source SHA-256: `dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979`
(checked against the upstream checksum). pgvector 0.8.2 tarball SHA-256 measured:
`69f4019389af05dc1c9548deb8628e62878e6e207c03907f2b8af2016472cdaa`.

The Python script exports **frozen, runtime-only** requirements with hashes from
`uv.lock`. Local `firra-worker` and `firra-contracts` are built as pure Python wheels
with their existing build backend; all third-party packages are installed with
`--only-binary :all: --require-hashes`. No compiler is used for dependency installation.
This includes psycopg-binary 3.3.6, cryptography 50.0.1, pydantic-core 2.46.5 and
the locked uvloop/httptools. All runtime dependencies had compatible wheels.
The package is launched with `python3.12 -I -B` (isolated environment and user site,
no bytecode writes), using its own site-packages, without an absolute-path venv.

The smoke re-enters through `env -i HOME=<temporary-home> PATH=/usr/bin:/bin`.
Native system Perl supplies timing and a 30-second per-command alarm so a launch
assessment stall returns nonzero; Python readiness is bounded to ten seconds.
Its data and socket directories are 0700, the socket is 0700, SQL uses a synthetic
`vector(768)`, and bundled psycopg checks `select 1` and vector dimensions. The
temporary cluster uses trust authentication within the private directory; this is
only a spike fixture, not an authentication design for L1-02. Cleanup stops the
cluster with `pg_ctl -m fast` and removes the temporary directory on failure too.

## Measurements

Single observations, not a statistical benchmark. Build caches were warm for Python.

| Measurement | Observed |
|---|---:|
| PostgreSQL source build, install, extension and signing | 91 s |
| Python copy, wheel build/install, pruning, thinning and signing | 21 s |
| First standalone Python download (separate initial step) | 7.70 s |
| PostgreSQL disk usage | 18,632 KiB (18.20 MiB) |
| Python disk usage | 119,524 KiB (116.72 MiB) |
| Whole bundle disk usage (`du -sk`) | **138,156 KiB (134.92 MiB)** |
| UDZO DMG bytes | **77,426,515 (73.84 MiB)** |

| Run | Fresh initdb | Postgres start | Python launch to first select 1 |
|---|---:|---:|---:|
| First-ever cluster, before Python thinning | 2.130 s | 0.445 s | 0.592 s |
| Final relocated bundle, unquarantined | 0.900 s | 0.188 s | 0.752 s |
| Final bundle, directory quarantine | 0.464 s | 0.140 s | 0.691 s |
| Final bundle, recursive quarantine | 0.664 s | 0.192 s | 1.776 s |

The table records the successful runs before the final re-sign; later failed
quarantined runs are described below. Each initdb used a new directory. "Cold" here means the first new cluster, not an
evicted OS filesystem cache. Timings include command launch overhead. Python's
first-select measurement includes runtime imports and connection establishment.

Idle RSS in the final unquarantined run: postmaster **10,688 KiB**, its idle child
processes **16,032 KiB** summed, Python **62,448 KiB** after imports and a closed
connection. RSS shares pages between processes; the sums are not unique physical
memory. PostgreSQL used 32 MiB shared_buffers. Python was held idle for two seconds.

## Relocation, signatures and no TCP

Every Mach-O was ad-hoc signed after install-name changes, libraries before
executables. No signed `.app` container was created (application packaging is L6-01).
The original runtime directories were renamed, including Python's download source,
before the final smoke. Both resulting runtime paths contain spaces.

This audit was run over the complete final bundle, not just the launch executables:

```bash
'apps/mac/build-engine/Engine Copy/python/bin/python3.12' -I -B - <<'PY'
from pathlib import Path
import re
import subprocess
count = 0
for binary in Path('apps/mac/build-engine/Relocated Engine').rglob('*'):
    if not binary.is_file() or binary.is_symlink():
        continue
    if 'Mach-O' not in subprocess.check_output(['file', '-b', str(binary)], text=True):
        continue
    assert subprocess.check_output(['lipo', '-archs', str(binary)], text=True).strip() == 'arm64'
    count += 1
    subprocess.run(['codesign', '--verify', '--strict', str(binary)], check=True)
    lines = subprocess.check_output(['otool', '-L', str(binary)], text=True).splitlines()[1:]
    for line in lines:
        dep = line.strip().split(' (compatibility')[0]
        assert dep.startswith(('/usr/lib/', '/System/Library/', '@loader_path/', '@rpath/')), (binary, dep)
    commands = subprocess.check_output(['otool', '-l', str(binary)], text=True)
    for rpath in re.findall(r'cmd LC_RPATH\n.*?path (.*?) \(offset', commands, re.S):
        assert rpath.startswith(('@loader_path/', '@executable_path/', '@rpath/')), (binary, rpath)
print(f'macho_count={count} architecture=arm64 signatures=valid load_paths=ok rpaths=relative')
PY
```

Output: `macho_count=71 architecture=arm64 signatures=valid load_paths=ok rpaths=relative`.
Symlinks resolve to the audited files. No Homebrew or `/usr/local` library path remained.
An additional `otool -l` scan found the highest declared macOS minimum was 14.0;
this does not replace executing the bundle on macOS 14.

The smoke checks the postmaster **and each child** with
`/usr/sbin/lsof -nP -a -p <pid> -iTCP -sTCP:LISTEN`, twice while running.
Each successful run printed:

```text
lsof_tcp_listeners=(empty)
python_select_1=ok runtime_imports=ok
waiting for server to shut down.... done
server stopped
smoke_engine=ok
```

The first lsof check was also empty. SQL `SHOW listen_addresses` returned the empty
string, and the vector query returned `768`. No cluster opened TCP. Removing
`postgres/lib/vector.dylib` temporarily produced exit 3, `could not access file
"$libdir/vector"`, and then `server stopped`; the library was restored immediately.

## Gatekeeper and problems encountered

Both the exact directory-quarantine test and the stronger recursive test exited 0.
The recursive test's `postgres` retained `0081;00000000;Safari;` when checked with
`xattr -p`. Terminal execution needed no interactive approval, no override, and no
quarantine removal for the quarantined runs. GUI dialogs were not independently
screen-observed; no process waited for an approval.

`spctl --status` returned `assessments enabled`. Assessment of the final recursively
quarantined executable returned exit **3** and exactly:

```text
apps/mac/build-engine/Relocated Engine/postgres/bin/postgres: rejected
```

After re-signing all libraries and then executables once more, the quarantined
Python stalled before reaching user code. A one-second `sample` showed only
`_dyld_start` and a 96 KiB footprint. Reapplying recursive quarantine did not restore
reliable launches. A later quarantined `initdb` also stalled; the bounded smoke
returned **142** after 30 seconds with `Alarm clock: 14`. This is consistent with
launch assessment blocking; an actual GUI dialog was not screen-observed.
The initial successful runs therefore do not prove reliable quarantined packaging.

The existing R1 fallback **did** work with no admin rights:

```bash
cp -R 'apps/mac/build-engine/Relocated Engine' 'apps/mac/build-engine/Engine Copy'
xattr -dr com.apple.quarantine 'apps/mac/build-engine/Engine Copy'
apps/mac/scripts/smoke-engine.sh 'apps/mac/build-engine/Engine Copy'
```

The final fallback smoke exited 0 with `python_select_1=ok runtime_imports=ok`,
empty TCP checks and `smoke_engine=ok`. Gatekeeper and SIP stayed enabled; only
the copied spike artifacts had quarantine removed. No production app startup
code or Application Support state was changed. Accepting the copy-and-clear path
changes the brief's direct-quarantined acceptance, so that decision is left to Claude.
Final copied-bundle timings: initdb 0.511 s, start 0.128 s, Python first select
0.730 s; postmaster RSS 10,688 KiB, children 16,032 KiB, Python 62,288 KiB.
The final signature audit used that unquarantined interpreter to inspect the
original quarantined bundle and again verified all 71 Mach-O files.

Problems solved: `--without-ssl` is not accepted by configure (omit it; TLS is off by
default); uv rewrites libpython's install identity to its install location (replace
it with `@loader_path`); Tcl libraries had bare install identities (normalize them
before auditing); uvloop's wheel carried universal2 slices (thin every Mach-O to
arm64); removing console scripts left dangling helper symlinks (retain only Python
interpreter names). An intermediate build was restarted after editing its running
script caused a shell parse error; completed build times exclude failed attempts.
Headers, static libraries, documentation, tests, caches and unused console scripts
are removed. Required timezone, PostgreSQL catalog and extension files are retained.

## Unverified and proposed app startup

- Clean user account or a Mac without developer tools/Homebrew/Python installed:
  **unverified**. The empty environment, relative library audit and hidden source
  directories remove runtime reliance on those tools; they do not replace that test.
- macOS 14, 15 and other releases: **unverified**; only macOS 26.5.2 was executed.
- Finder download/DMG drag-and-drop, right-click Open, signed outer app, child
  launch from Swift, hardened runtime and notarization: **unverified/out of scope**.
- Long-running worker operation, migrations/shim, recovery, upgrades and all
  worker module imports beyond the required runtime smoke: later packages.

Proposal for L1-02/L5-01: keep binaries read-only under
`Firra.app/Contents/Resources/engine/{postgres,python}`. The app initializes
`~/Library/Application Support/Firra/pg` (0700) once, then launches bundled Postgres
as its child with no TCP. Use a short private per-user socket path (the Unix socket
path length is limited; the full Application Support path can be too long). Engine
Python is another app child, launched with `-I -B`, connecting through that socket;
its FastAPI endpoint later uses a random **127.0.0.1** port with the agreed token
handshake. Keep state and content-free logs under Application Support, secrets in
Keychain/memory. On quit, stop Python and `pg_ctl -m fast`, then remove the socket
directory. Final socket placement, supervisor and secret handling belong to those
briefs, not this spike. If Claude accepts R1, the app copies the engine to its
private Application Support directory, clears quarantine on that copy, then starts
the copied binaries. This fallback is proven only with spike artifacts on this Mac;
the clean-install and real app checks still remain.
