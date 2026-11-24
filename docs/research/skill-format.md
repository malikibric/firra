# Skill format research (L4-06)

Verified 2026-10-05. CONFIRMED = primary source verbatim. UNVERIFIED = could
not confirm from a primary source.

## SKILL.md format (CONFIRMED)

Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
(same rules on https://code.claude.com/docs/en/skills for Claude Code).

- Quote: "Every Skill requires a `SKILL.md` file with YAML frontmatter" with
  "Required fields: `name` and `description`".
- Quote: "`name`: Maximum 64 characters; Must contain only lowercase letters,
  numbers, and hyphens; Cannot contain XML tags; Cannot contain reserved words:
  'anthropic', 'claude'".
- Quote: "`description`: Must be non-empty; Maximum 1024 characters; Cannot
  contain XML tags" and "must include both what the Skill does and when Claude
  should use it".
- Discovery: "Claude loads this metadata at startup and includes it in the
  system prompt" (~100 tokens per skill); the body loads when triggered.
- Our skill: `name: firra`, one-sentence description with what + when, 66-line
  body. Fits.

## Install with `npx skills add` (CONFIRMED)

Source: https://www.npmjs.com/package/skills (the `skills` CLI, MIT) and
https://github.com/NVIDIA/skills/blob/main/docs/advanced-install.md.

- Quote: `npx skills add vercel-labs/agent-skills --skill frontend-design
  -g -a claude-code -y` (flags: `-s/--skill`, `-a/--agent`, `-g/--global`,
  `-y` non-interactive, `-l/--list`).
- Destinations (CONFIRMED): Claude Code `~/.claude/skills/` (global) and
  `.claude/skills/` (project); Codex `~/.codex/skills/`; Cursor
  `~/.cursor/skills/`. Manual copy to those paths is the documented fallback.
- Our README uses `npx skills add /path/to/fira --skill firra` (local-path
  source, CONFIRMED supported) plus the manual fallback.

## Which clients read skills (mixed)

- Claude Code reads `~/.claude/skills/` and `.claude/skills/` (CONFIRMED,
  platform docs above). Codex reads `~/.codex/skills/` (CONFIRMED, npx skills
  docs). Cursor reads `~/.cursor/skills/` (CONFIRMED, npx skills docs).
- claude.ai custom skills upload (zip, Pro+ plans) and ChatGPT distribution go
  through vendor review portals (CONFIRMED, docs/research/mcp-clients.md) —
  filesystem install does NOT reach web/mobile clients.
- MCP `instructions` field: read by Codex (CONFIRMED, L0-05 research).
  Prompts-as-slash-commands, sampling, elicitation per client: UNVERIFIED
  (L0-05; the skill text itself carries the rules, so nothing depends on them).

## Could not verify (UNVERIFIED)

- Whether `npx skills` CLI version on the owner's Mac supports our exact flag
  mix without update (`npx skills --version`, upgrade to latest if unsure).
- Cursor skill pickup without restart (docs say reload; exact gesture varies).
- That `/reload-skills` exists in the owner's Claude Code version.
