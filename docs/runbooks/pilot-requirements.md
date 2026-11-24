# Pilot partner requirements (L10-03 fact-check prep)

What a pilot partner needs, one source line per claim. Sources: `docs/plans/local-v1.md`
(plan), `docs/research/google-local.md` (S1), `docs/research/mcp-clients.md` (S4a),
`docs/research/ai-cli.md` (S3a), `docs/research/slack-local.md` (L2S-01).

## The machine

- Apple Silicon Mac. Source: plan T12 ("Apple Silicon only, macOS 14+") and
  plan §1 ("on a clean Apple Silicon Mac (macOS 14+)").
- macOS 14 or newer. Source: plan T12.
- FileVault on (or accept the app's warning). Source: plan T11 ("FileVault
  required (warning if off)").

## The mailbox

- A Gmail account the partner owns; Firra reads only that mailbox and
  calendar. Source: plan §1 item 2 (onboarding) and §2.1 (one user, no teams).
- No Workspace admin needed for Gmail. Source: Desktop OAuth runs in the
  user's own browser session (S1); admin allowlisting only matters if their
  org blocks third-party apps (S1 §8).
- G1 skipped: the partner's real mail is the first real mail. Source: plan
  §2.1 ("first real mail is the first pilot partner").

## The AI (pick at least one)

- Recommended: Claude Code or Codex installed and signed in, for background
  extraction. Source: plan §1 item 4 and D1 (user's AI).
- Fallback: neither — processing happens in conversation instead. Source:
  plan §1 item 4 ("or in conversation if they have neither").
- A Claude or ChatGPT account for the MCP link. Source: plan §1 item 7 and
  L10-03 ("a Claude or ChatGPT account"); S4a matrix for per-client setup.
- Consumer AI plans may train on conversations unless switched off; onboarding
  says so. Source: S3a §9 and plan guardrails.

## Slack (optional, Gmail-only pilot is fine)

- Optional: a workspace where the partner can create an internal app (member
  by default; owner approval if the workspace requires it). Source: L10-03
  ("optionally Slack with the right to create an internal app") and L2S-01
  §3.
- G-S note: Slack follows when its gate passes; the pilot may start Gmail-only.
  Source: plan gate G-S.

## Google test users (owner does this, not the partner)

- The partner's address is added as a Google test user before connecting.
  Source: plan owner tasks ("add test users") and S1 §5 (Testing: max 100
  test users, 7-day refresh expiry).

## Who fits

- A founder who talks to clients (agency/consultancy, 15–150 people per the
  product definition; founder-led first). Source: plan §1 product paragraph
  and owner task ("a founder who talks to clients").
- 2–3 partners total for the pilot. Source: plan owner tasks ("Find 2–3
  pilot partners").

## What we ask of them weekly

- A metrics export sent by hand (content-free). Source: plan pilot phase
  ("weekly metrics export") and L10-03.
- Keep notifications on for 2 weeks; rate `important` items. Source: routines
  exit criteria referenced in plan §F5/E.
