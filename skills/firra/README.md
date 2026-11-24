# Firra skill

How an AI should use Firra's MCP: which tool for which question, how to cite
evidence, what never to say. For Claude Code, Codex, and Cursor.

## Install

From the Firra repo (the skill lives at `skills/firra/`):

```bash
npx skills add /path/to/fira --skill firra -a claude-code -a codex -a cursor
```

Or copy `skills/firra/` by hand:

- Claude Code: `~/.claude/skills/firra/`
- Codex: `~/.codex/skills/firra/`
- Cursor: `~/.cursor/skills/`

Then reload skills (`/reload-skills` in Claude Code, or restart the session).

## Test

1. Connect Firra first (local MCP: `firra-mcp` over stdio; link relay for web/mobile).
2. Paste the 10 prompts from the SKILL.md test plan into Claude Code.
3. Pass: every answer cites `fact_id` or evidence ids, never contradicts a
   Firra state, play, or importance, keeps third-party text inside
   `untrusted_quote`, and asks for confirmation before any write.
