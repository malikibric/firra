# Security review (L8-07), first pass 2026-10-05

Lead review of Local v1 as built. Evidence is a test or a code location; **open** items are not fixed. A second pass is due after the relay is deployed
and after F9 (cloud code removal), and an independent look at the executor sandbox and the relay is still wanted before a public launch (pilot waiver: none needed for 2-3 known partners).

## Assets and trust boundaries

| Asset | Where | Boundary |
|---|---|---|
| Mail and chat text, subjects, meeting titles | local Postgres, AES-GCM per field (`firra/local/bodycrypt.py`), key from the Keychain | the Mac; FileVault is the second layer |
| Google refresh token, Slack user token, body key, device key | Keychain; engine memory only | app to engine over a 0600 unix socket (peer pid checked) |
| Local API token | memory + `run/engine.json` (0600) | 127.0.0.1, bearer, Host and Origin checks |
| Connector link secrets | shown once; sha256 only in `settings.json` (0600) and relay memory | public internet (relay) |
| Owner's AI | Claude Code CLI as a child process; MCP clients | text leaves the Mac only to the owner's chosen AI and provider APIs |

## Checklist

| # | Area | Verdict | Evidence / note |
|---|---|---|---|
| 1 | No secret on disk or in logs | ok | `tests/oracle/local/test_encryption_oracle.py` (searches the whole data dir), `tests/integration/test_log_hygiene_engine.py` |
| 2 | Raw provider payloads and plain subjects at rest | ok (fixed S0) | `NoRawStore`; `tests/integration/test_local_at_rest.py`; legacy rows repaired at start |
| 3 | Control socket and token files | ok | `tests/oracle/local/test_control_oracle.py` (peer pid, 0600, 0700 dir) |
| 4 | Local API exposure | ok | loopback only, bearer, Host/Origin, 2 MiB body limit: `tests/unit/local/test_server.py` |
| 5 | Policy at every read path (`excluded`, `metadata_only`) | ok | L4-04 MCP oracle; source layer oracle `test_source_layer_oracle.py` (11 cases) |
| 6 | Prompt injection through mail/Slack into the user's AI | partly | `untrusted_quote` / `untrusted_text` wrapping, bounded excerpts, control characters stripped, no side-effect tools except throttled sync, writes need confirmation. Residual: the user's AI may still follow instructions inside excerpts; mitigated by the skill and the switch being off by default |
| 7 | Executor sandbox (background extraction) | ok for Claude Code; Codex **refuses by default** (accepted risk, not offered) | `firra/ai/claude_code.py`: no tools, no MCP, no settings, no session persistence, empty temp cwd, minimal env, bounded output; tests with a fake CLI. `firra/ai/codex.py` (2026-10-09): Codex has no switch to disable tools and `-s read-only` still allows reads (`docs/research/ai-cli-probes.md`), so `CodexExecutor.run` raises before spawning anything unless `accept_read_only_sandbox=True`; nothing sets it (`test_nothing_in_the_engine_turns_the_acceptance_on`) and `AiRuntime.update` still answers `codex_not_available`. When accepted it adds `-s read-only --ignore-user-config --ignore-rules --skip-git-repo-check --ephemeral`, empty private cwd, 5-variable environment, stdin only, capped output, process group killed; `tests/unit/ai/test_codex.py` (17 tests, each restriction mutation-checked: dropping a flag or flipping the default fails a test) |
| 8 | Slack token handling | ok | Keychain slot, `auth.test` validation, `auth.revoke` on disconnect, too-broad token refused (L2S-03 tests) |
| 9 | Backup | ok | scrypt + AES-GCM, wrong phrase refused (L8-02 test), only non-recoverable state |
| 10 | Deletion | ok | `tests/integration/test_delete_everything_oracle.py` |
| 11 | Relay: device identity | ok | Ed25519 challenge; id = sha256(public key) prefix; replayed signature refused (`services/relay/tests/oracle`) |
| 12 | Relay: link secrets | ok with a known risk | 256 bit, hash only on the Mac and in relay memory, engine rechecks, revocation immediate, secret never logged. Risk: the link is a bearer secret held by the AI vendor; documented in `relay.md` §8 and shown to the owner when a link is created |
| 13 | Relay: scopes | ok | `sources` needs the link scope and the owner switch (`test_connector_link_scope_limits_source_tools`); the relay cannot inject scopes (header allow-list) |
| 14 | Relay: abuse | ok (fixed, three more gaps closed 2026-10-09) | per-device 120/min, per-IP 240/min, 30 socket attempts per IP per minute (refused before accept), 2,000 devices, 8 in flight, 1 MiB frames, 512 KiB bodies, deadlines; `test_connection_attempts_per_address_are_limited`, oracle `test_rate_limit_130_calls_gets_429_with_retry_after`. New: (a) the per-address key was uvicorn's view of `X-Forwarded-For` with `forwarded_allow_ips="*"`, which takes the **first, caller-supplied** entry, so one client could dodge every per-IP limit; now `client_ip` uses the last entry (appended by Cloud Run) and uvicorn's proxy handling is off (`server_config`, shared by `main` and the tests; `test_forwarded_for_entries_before_the_last_cannot_dodge_the_address_limit`); (b) a chunked upload with no `Content-Length` was buffered completely before the 413 check, by anyone who could form a token-shaped URL; now read in a stream and cut at 512 KiB (`test_chunked_upload_is_cut_off_at_the_body_limit`); (c) a full frame queue made `Hub.unregister` raise and skip the other in-flight requests, which then waited for their deadline; terminal frames now always fit (`test_a_full_queue_still_hears_that_the_device_is_gone`). Assumption to confirm at deploy: the relay is reached directly through Cloud Run's front end; behind another proxy the last entry is that proxy and clients share one budget (strict, not open) |
| 15 | Relay: availability | accepted | single instance, grants in memory (the Mac resends them at connect) |
| 16 | Update integrity | partly | built: the app checks an https-only `latest.json` once a day (size cap, strict version/URL/hash validation, `UpdatesTests`), shows the new version, download link and the SHA-256 to verify; it never downloads or installs. `scripts/publish-release.sh` writes the feed. **Open:** the release bucket (Terraform) and its address; the check stays off until `FIRRA_UPDATE_FEED` is set at build time |
| 17 | App signing | accepted | ad-hoc/local certificate, quarantine cleared on the engine copy (R1); no notarization (owner decision) |
| 18 | Supply chain | partly | verified 2026-10-09: pnpm `minimumReleaseAge` 7 days, `blockExoticSubdeps`, `trustPolicy: no-downgrade`, install scripts allow-listed (`pnpm-workspace.yaml`); `uv.lock` carries sha256 hashes and `uv sync --frozen` is used by `check.sh` and the relay Dockerfile; the Mac package has no third-party dependency; `scripts/check.sh security`: gitleaks (`origin/main..HEAD`) and semgrep (p/default, typescript, python, dockerfile, terraform) pass. **Open (not changed, a policy choice for the lead):** no release-age gate for Python packages (`exclude-newer` is not set), and the relay Dockerfile base images (`python:3.12-slim`, `uv:0.12`) are pinned by tag, not by digest |
| 19 | Slack export import | ok pending oracle | in-memory zip read, traversal, symlink, duplicate, size caps (`firra/connectors/slack/export.py`); independent oracle L2S-12o in progress |
| 20 | Metrics and diagnostics exports | ok | content-free tests (`test_local_routes_maintenance.py`); no workspace id in metrics |

## Open items, in priority order

1. Create the release bucket and set `FIRRA_UPDATE_FEED` in the DMG build (item 16) before any partner outside the owner's circle; owner go and a bucket address are needed.
2. Codex executor (item 7): built but refuses to run. To offer it, probe file-read attempts under `-s read-only` on a real CLI (needs the owner's go), decide on the residual risk with the owner, then wire `accept_read_only_sandbox` to an explicit acknowledgement in the AI settings. Until then it is not offered.
3. Independent review of the Claude Code sandbox flags against the current CLI release (flags change between versions): re-run the probe in `docs/research/ai-cli-probes.md` at every CLI major update.
4. Stage 2 OAuth: a separate threat review before it replaces links.
