# Research S3a: headless Claude Code / Codex as extraction engine (L0-06)

Date read: 2026-10-04. Tool versions on test Mac: `claude 2.1.289 (Claude Code)`, `codex-cli 0.160.0`. Tags: CONFIRMED = vendor doc or `--help` verbatim. UNVERIFIED = not found / secondary / needs L3-08 probe. No prompt was sent to any model (per brief).

## 1. Terms — automated / scheduled / background use of subscriptions

**Short answer: no explicit "no automation" sentence was found in the fetched excerpts; headless use is a documented invocation mode, but heavy background use is rate-limited and throttled. The exact contractual boundary is UNVERIFIED — read the full Consumer Terms / ChatGPT Terms of Use before building.**

Claude Code (Anthropic):
- Quote: "With Pro and Max plans, you now have access to both Claude on the web, desktop, and mobile apps and Claude Code in your terminal with one unified subscription."
  - URL: https://support.claude.com/en/articles/11145838-claude-code-overview — 2026-10-04 — CONFIRMED
- Quote: "Both Pro and Max plans offer usage limits that are shared across Claude and Claude Code, meaning all activity in both tools counts against the same usage limits."
  - Same URL — CONFIRMED
- Quote: "Allow your usage period to reset before continuing to use Claude Code. Monitor your remaining allocation using the /status command."
  - URL: https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan — 2026-10-04 — CONFIRMED
- Background-24/7 use is discouraged by rate policy (secondary reporting of Anthropic announcement):
  - Quote: "Anthropic spokesperson Amie Rotherham ... 'most users won't notice a difference,' and ... this limit will affect less than 5% of subscribers" + "new weekly rate limits ... to curb usage among subscribers who are running ... Claude Code, 'continuously in the background, 24/7.'"
  - URL: https://techcrunch.com/2025/07/28/anthropic-unveils-new-rate-limits-to-curb-claude-code-power-users — 2026-10-04 — UNVERIFIED (press, not terms)
- 5-hour rolling window + weekly active-hours cap, shared across Claude Code + claude.ai + Cowork; plan ranges (Pro ~10–45 prompts/window, ~40–80 Sonnet hrs/week; Max higher) are secondary estimates:
  - URL: https://www.truefoundry.com/blog/claude-code-limits-explained + https://claudecode101.com/en/faqs/claude-code-usage-limits — 2026-10-04 — UNVERIFIED (numbers change; authoritative check is `/status` + account settings)
- "Automated or scripted use" prohibition: UNVERIFIED — not found in fetched help/docs. Settle by reading Consumer Terms (Free/Pro/Max) + Team/Enterprise terms in full + asking Anthropic support whether schedule-driven `claude -p` extraction on the user's own machine under their login is permitted. If forbidden → fallback to L3-04 in-conversation only (Stop-and-ask trigger).
- Plan types where headless unavailable: UNVERIFIED. Free tier has no Claude Code access per secondary table (UNVERIFIED); Team/Enterprise have separate doc path ("see Use Claude Code with your Team or Enterprise plan") — confirm during onboarding copy review.

Codex (OpenAI):
- Quote: "Codex is included across ChatGPT plans, including Free and Go. Usage limits vary by plan."
  - URL: https://help.openai.com/en/articles/11369540-codex-in-chatgpt-usage-limits-and-billing — 2026-10-04 — CONFIRMED
- Quote: "What terms govern your use of Codex? When you sign in to Codex using an existing ChatGPT account, the ChatGPT Terms of Use and Privacy Policy ... apply to data shared between Codex and ChatGPT."
  - Same URL — CONFIRMED
- Quote: "Local messages and cloud chats share your plan's usage allowance. Weekly limits may also apply." + "You can find your current limits in the usage dashboard. ... in an active Codex CLI session, enter /status."
  - URL: https://developers.openai.com/codex/pricing — 2026-10-04 — CONFIRMED
- Automated/scheduled/scripted-use ban: UNVERIFIED — not found in fetched excerpts. Same settle path: full ChatGPT Terms of Use + developer terms. Note API-key mode exists explicitly "Great for automation in shared environments like CI" (pricing page, CONFIRMED) — the sanctioned automation path is API billing, not subscription scripting.

## 2. Headless invocation (exact commands, no model calls made)

### Claude Code 2.1.289
- Non-interactive flag (from local `claude --help`, 2026-10-04 — CONFIRMED):
  - Quote: "Claude Code - starts an interactive session by default, use -p/--print for non-interactive output"
  - Quote: "--output-format <format> Output format (only works with --print): \"text\" (default), \"json\" (single result), or \"stream-json\" (realtime streaming)"
  - Quote: "--json-schema <schema> JSON Schema for structured output validation."
  - Quote: "--input-format <format> Input format (only works with --print): \"text\" (default), or \"stream-json\""
  - Quote: "--model <model> Model for the current session."
- Prompt passing: `claude -p "<prompt>"` (argument) — CONFIRMED by usage line `Usage: claude [options] [prompt]` + `Arguments: prompt Your prompt`. stdin size limits: UNVERIFIED (probe in L3-08 with fixture sizes; keep packets ~20 mails).
- JSON-only: `--output-format json` + `--json-schema '<schema>'` — CONFIRMED flags exist; schema enforcement strictness (reject vs coerce) UNVERIFIED — L3-08 must feed malformed-model-output fixtures (recorded, no live call) plus one live malformed test later.
- Timeout: no `--timeout` flag observed in `--help` excerpt — UNVERIFIED. Parent process must enforce timeout (e.g. `timeout(1)` / subprocess deadline) and treat kill as retriable. CONFIRMED approach, UNVERIFIED exact flag absence (re-check full help in L3-08).
- Empty working directory: `-C`? Not observed for claude; use process cwd = fresh `mkdtemp` + `--add-dir` omitted. Exact cwd flag: UNVERIFIED — confirm in L3-08 (`--help` full text).

Recommended headless shape (to verify in L3-08, no live call yet):
`claude -p "$PROMPT" --output-format json --json-schema "$SCHEMA" --model <haiku-class> --tools "" --strict-mcp-config --settings /dev/null-minimal --permission-mode manual --no-session-persistence` in empty temp cwd with scrubbed env. Every flag above except `--no-session-persistence`/`--model`/`--tools ""`/`--strict-mcp-config` needs re-verification against full help (names change fast).

### Codex CLI 0.160.0
- Non-interactive (from local `codex exec --help`, 2026-10-04 — CONFIRMED):
  - Quote: "Run Codex non-interactively" / "Usage: codex exec [OPTIONS] [PROMPT]"
  - Quote: "[PROMPT] Initial instructions for the agent. If not provided as an argument (or if `-` is used), instructions are read from stdin. If stdin is piped and a prompt is also provided, stdin is appended as a `<stdin>` block"
  - Quote: "--output-schema <FILE> Path to a JSON Schema file describing the model's final response shape"
  - Quote: "--json Print events to stdout as JSONL"
  - Quote: "-o, --output-last-message <FILE> Specifies file where the last message from the agent should be written"
  - Quote: "-C, --cd <DIR> Tell the agent to use the specified directory as its working root"
  - Quote: "-m, --model <MODEL> Model the agent should use"
  - Quote: "-s, --sandbox <SANDBOX_MODE> Select the sandbox policy ... [possible values: read-only, workspace-write, danger-full-access]"
  - Quote: "--ephemeral Run without persisting session files to disk"
  - Quote: "--ignore-user-config Do not load `$CODEX_HOME/config.toml`" / "--ignore-rules Do not load user or project execpolicy `.rules` files"
  - Quote: "--skip-git-repo-check Allow running Codex outside a Git repository"
- Prompt size limits (arg vs stdin): UNVERIFIED — probe with fixture sizes in L3-08.
- Timeout flag: none observed — parent enforces. UNVERIFIED (re-check full help).

Recommended headless shape (to verify in L3-08):
`codex exec -s read-only --ignore-user-config --ignore-rules --skip-git-repo-check --ephemeral -C "$EMPTY_TMP" --output-schema extract.schema.json --json -o last.json - < packet.txt` with scrubbed env. Confirm network/file containment of `read-only` in L3-08 (see §3).

## 3. Locking it down (no tools, no files, no network, no MCP/skills/hooks, no secrets)

### Claude Code
CONFIRMED flags (local `--help` 2026-10-04):
- Quote: "--tools <tools...> Specify the list of available tools from the built-in set. Use \"\" to disable all tools, \"default\" to use all tools, or specify tool names"
- Quote: "--strict-mcp-config Only use MCP servers from --mcp-config, ignoring all other MCP configurations"
- Quote: "--mcp-config <configs...> Load MCP servers from JSON files or strings (space-separated)" (so: pass empty/none + `--strict-mcp-config`)
- Quote: "--settings <file-or-json> Path to a settings JSON file or a JSON string to load additional settings from"
- Quote: "--restricted Restricted mode: removes the built-in tools that run commands or code (Bash, PowerShell, REPL and the other code-running tools) and WebFetch unless --tools names them, and ignores user, project and local settings files ... Also confines the file tools to the working directories (--add-dir included)"
- Quote: "--bare Minimal mode: skip hooks ..., LSP, plugin sync, attribution, auto-memory, background prefetches, keychain reads, and CLAUDE.md auto-discovery."
- Quote: "--disallowedTools, --disallowed-tools <tools...> Comma or space-separated list of tool names to deny"
- Quote: "--permission-mode <mode> Permission mode to use for the session (choices: \"acceptEdits\", \"auto\", \"bypassPermissions\", \"manual\", ...)"
- Quote: "--add-dir <directories...> Additional directories to allow tool access to" (so: omit it)
Safest composition for extraction: `--tools ""` + `--strict-mcp-config` (with no `--mcp-config`) + `--restricted` (or `--bare` — pick one after L3-08 comparison) + `--settings <locked-minimal>` + empty temp cwd + scrubbed env + `--permission-mode manual` (never bypass). Whether `--restricted` + `--tools ""` compose or conflict: UNVERIFIED — probe in L3-08.
- What happens if the model tries a tool anyway: UNVERIFIED (must observe: denied error vs approval hang — the hang case would break background use; L3-08 injection fixture decides).
- CLAUDE.md/AGENTS.md ignorance: `--restricted` ignores user/project/local settings; `--bare` skips CLAUDE.md auto-discovery — CONFIRMED flags, UNVERIFIED equivalence for project CLAUDE.md vs settings. L3-08 must plant a malicious `CLAUDE.md`/`AGENTS.md` in a parent dir and prove non-ingestion.
- Skills/plugins/hooks: `--bare` skips hooks + plugin sync; `--disable-slash-commands Disable all skills` — CONFIRMED flag text. Full plugin/hook suppression matrix: UNVERIFIED — L3-08.
- Env secrets: `--bare` mentions keychain handling; general env scrubbing is parent-side (`env -i` + allowlist `PATH/HOME` minimal, no `ANTHROPIC_API_KEY` leakage, no Firra secrets). UNVERIFIED that CLI never exfiltrates env on its own — L3-08 + log review.

### Codex
CONFIRMED flags (local `--help` 2026-10-04): `-s read-only`, `--ignore-user-config`, `--ignore-rules`, `--ephemeral`, `-C`, `--skip-git-repo-check`. What `read-only` actually blocks (file read? file write? network? subprocess?): UNVERIFIED — the help line only says "sandbox policy ... when executing model-generated shell commands". L3-08 must test: read attempt, write attempt, network attempt, subprocess attempt from an injected mail, under `read-only` with tools disabled (exact tool-disable mechanism for codex exec: UNVERIFIED — find `--disable-*`/config `tools` off switch in full help/config reference; if none exists → Stop-and-ask, design changes).
- MCP/skills/hooks suppression for codex exec: UNVERIFIED — same probe as Claude (plant + `--ignore-user-config`/`--ignore-rules`).
- Approval hang risk: `--approve-for-me` routes approvals automatically (do NOT use for extraction); default approval behaviour when a tool is attempted headless: UNVERIFIED — L3-08 must confirm fail-closed without hanging.

## 4. Session data (where mail text lands on disk)

- Claude Code: transcripts under `~/.claude/projects/...` — CONFIRMED (directory listed on test Mac 2026-10-04). Suppression: Quote: "--no-session-persistence Disable session persistence - sessions ... resumed (only works with --print)" — CONFIRMED flag exists; UNVERIFIED whether it suppresses ALL disk writes (transcripts, logs, cache) for `-p` runs. Assume mail text reaches `~/.claude` until L3-08 proves otherwise; onboarding must disclose this.
- Codex: sessions under `~/.codex/sessions/...` — CONFIRMED (directory listed 2026-10-04). Quote: "--ephemeral Run without persisting session files to disk" — CONFIRMED flag; UNVERIFIED coverage (logs? caches?). Same disclosure rule.
- Config/auth paths (`~/.claude`, `~/.codex`, `CODEX_HOME`): auth material lives there; engine must never read/write those dirs except executing the binary. CONFIRMED existence, UNVERIFIED full file inventory — L3-08 log/file-watch test.

## 5. Failure modes (how the parent tells them apart)

UNVERIFIED matrix — L3-08 must capture exit codes + stderr markers WITHOUT model calls where possible (not-installed, bad flags, no-login via scrubbed `HOME`/offline), and with recorded-output replay for the rest:

| Condition | Claude signal (expected, UNVERIFIED) | Codex signal (expected, UNVERIFIED) | Parent action |
|---|---|---|---|
| not installed | spawn ENOENT (exit 127) | same | status "not installed" → onboarding |
| not logged in / expired login | non-zero + login hint on stderr | non-zero + sign-in hint | status "not signed in" → onboarding; re-auth, no retry loop |
| rate limit / usage cap | non-zero + "limit"/"reset" text; `/status` in CLI | non-zero + limit text; `/status` | status "limit" → pause until reset, queue visible, no busy retry |
| network down | non-zero, retryable | non-zero, retryable | backoff + retry, scheduler keeps order |
| timeout (parent kill) | 124 / signal | same | retriable once, then park packet |
| malformed JSON output | `--json-schema` validation error path | `--output-schema` mismatch | reject packet, count, verify path already drops non-verbatim quotes |
| prompt too large | pre-flight size check (parent) | same | split packet, never truncate silently |

Reliable discriminator = (exit code, stderr regex, duration) triple recorded in L3-08; no string-matching on model output.

## 6. Detection without running a prompt

- Claude: `claude auth status` prints JSON with login state — CONFIRMED (ran help-level status on test Mac 2026-10-04: `{"loggedIn": true, "authMethod": "claude.ai", ...}`). Version: `claude --version` → `2.1.289` CONFIRMED. Exact stable fields for parsing (`loggedIn`, `authMethod`): UNVERIFIED contract — parse defensively.
- Codex: `codex login status` → `Logged in using ChatGPT` — CONFIRMED (test Mac 2026-10-04). Version: `codex --version` → `codex-cli 0.160.0` CONFIRMED. Machine-readable status flag: UNVERIFIED — check `codex login status --json` (or equivalent) in L3-08; fall back to string match + exit code.
- Engine detection plan (no prompt): binary present → `--version` parses → auth-status command → route (background CLI vs in-conversation-only). All three steps are free.

## 7. Cost and size

- Default-model context windows: UNVERIFIED in this pass (secondary: Sonnet 1M window from 2026-06-30 per community post — UNVERIFIED, do not size against it). Settle from `code.claude.com/docs` model table + `codex --help`/model list during L3-08.
- Practical batch: ~20 mails × ~2 KB ≈ 40 KB ≈ ~10–15k tokens + instructions/schema — fits any current frontier window, but the binding constraint is SUBSCRIPTION rate limits (§1), not context. Batch size is a cost/latency choice for L3-07, not a fit choice.
- Cheaper/faster model selection: `--model` (both CLIs) — CONFIRMED flags. Haiku-class names for 2026-10: UNVERIFIED — resolve at build time from `--help`/docs (never hardcode per CLAUDE.md §7 analogue; engine config maps names).

## 8. Alternatives (supported user-subscription paths, API keys)

- Claude Agent SDK / `codex` SDK / app-server mode as the sanctioned subscription path: UNVERIFIED in this pass (not checked; brief allows mention only if obviously relevant — it was not obvious from fetched docs, so left for L3-08/L3-01 to confirm or drop).
- User-pasted API key: supported technically but changes billing/terms:
  - Quote: "Important: If you have an ANTHROPIC_API_KEY environment variable set on your system, Claude Code will use this API key for authentication instead of your Claude subscription (Pro, Max, Team, or Enterprise plans), resulting in API usage charges rather than using your subscription's included usage."
  - URL: https://support.claude.com/en/articles/11145838-claude-code-overview — 2026-10-04 — CONFIRMED
  - Codex API-key mode: Quote: "All users may also run extra local tasks using an API key, with usage charged at standard API rates." + "API Key: Great for automation in shared environments like CI." — URL: https://developers.openai.com/codex/pricing — 2026-10-04 — CONFIRMED
  - Recommendation: subscription-first (D1), API key as explicit opt-in fallback with spend warning; never silently prefer env keys (scrub env in executor).

## 9. Risks and recommendation

| Risk | Severity | Mitigation in plan |
|---|---|---|
| Subscription terms forbid scripted/scheduled use (UNVERIFIED) | design-breaking | read full terms + support ticket now; keep L3-04 in-conversation path as the guaranteed fallback |
| 5-hour + weekly caps stall backfill (CONFIRMED mechanism, UNVERIFIED numbers) | high | 3-month default, newest-first, pause-until-reset, visible queue (L3-07); one-real-run eval (L3-06) with owner go-ahead only |
| No flag combo fully disables tools/files/network/MCP (UNVERIFIED until L3-08) | design-breaking | L3-08 injection fixtures decide; Stop-and-ask if unfixable |
| Mail persists in `~/.claude` / `~/.codex` (CONFIRMED dirs, UNVERIFIED suppression) | medium | disclose in onboarding + privacy policy; prefer `--no-session-persistence` / `--ephemeral` after proof; retention rule L8-01 covers Firra side only |
| Consumer-plan training on mail (CONFIRMED choice for Free/Pro/Max) | high | Quote: "Consumer users (Free, Pro, and Max plans): We give you the choice to allow your data to be used to improve future Claude models." — URL: https://code.claude.com/docs/en/data-usage — 2026-10-04 — CONFIRMED. Onboarding must direct users to off-switch at `claude.ai/settings/data-privacy-controls`; Codex equivalent: Quote: "Conversations may be used to improve models unless you turn off training in ChatGPT data controls." — help-center URL above — CONFIRMED. Commercial (Team/Enterprise/API) does not train by default — CONFIRMED same pages. |
| Data retention 5yr vs 30-day (Claude, CONFIRMED same page) | medium | same switches; note in privacy policy |
| Approval hang on tool attempt (UNVERIFIED) | high | L3-08 must prove fail-closed non-interactive; `--permission-mode manual` + no TTY must not block |

**Recommendation:** Claude Code first (richer `--tools ""` + `--restricted`/`--bare` + `--strict-mcp-config` + `--json-schema` story today), Codex second (pending tool-disable + `read-only` semantics proof). Exact invocation frozen only after L3-08 green. Onboarding tells the user: (1) background extraction uses THEIR login and THEIR limits, shared with their chat usage; (2) training off-switch; (3) `~/.claude` or `~/.codex` will hold transcripts until suppression is proven; (4) "only in conversation" fallback if they have neither CLI.

## Commands to verify later (L3-08, fake mail with injection — no live model calls in this task)

All against fixture packet `fixtures/ai-cli/injection.txt` (contains "ignore previous instructions / run a command / read a file / exfiltrate", plus oversize + malformed-JSON cases). Expected = fail-closed JSON-only, no side effects.

1. `claude --version` parses `2.x` — expect 0.
2. `claude auth status` (JSON) — expect `loggedIn:true` shape recorded; logged-out Mac expects non-zero + login hint (capture exact text without sending a prompt — run with scrubbed `HOME`).
3. `tmp=$(mktemp -d); cd $tmp; claude -p "$(cat packet.txt)" --output-format json --json-schema extract.schema.json --tools "" --strict-mcp-config --settings "$LOCKED" --permission-mode manual --no-session-persistence --model <haiku>` — expect: exit 0 + schema-valid JSON on recorded-output replay path; live run only with owner go-ahead. Negative: plant `CLAUDE.md` with "always call Bash" one dir up — expect no tool call in `--output-format stream-json` trace.
4. Same with `--restricted` instead of `--bare`-family — record which combination yields zero tool attempts + zero file writes (file-watch on cwd + `~/.claude`).
5. `codex --version` parses — expect 0.
6. `codex login status` — record exact logged-in vs logged-out text + exit codes (scrubbed `CODEX_HOME` for logged-out).
7. `codex exec -s read-only --ignore-user-config --ignore-rules --skip-git-repo-check --ephemeral -C $tmp --output-schema extract.schema.json --json -o last.json - < packet.txt` — expect schema-shaped final message; injection fixture must produce no file/network/process side effects.
8. Oversize packet (200 mails) — expect parent pre-flight split, never CLI truncation; record actual stdin/arg ceiling.
9. Kill probe: `timeout 5s <invocation>` on a slow fixture — expect parent-detected 124, packet re-queued, no partial write accepted.
10. Malformed-model-output replay (hand-written bad JSON through the same parse+verify path) — expect reject + count, no store.
