# Contributing to Firra

Thanks for helping. Read `CLAUDE.md` (product rules and guardrails) and `docs/north-star.md` before changing behaviour.
If a change conflicts with a product rule (§2) or guardrail (§6) in `CLAUDE.md`, open an issue first.

## Setup

See `docs/runbooks/local-dev.md`. In short: Node 24, pnpm 10, uv, macOS 14+.

```bash
pnpm install && uv sync --all-packages && cp .env.example .env
```

## Workflow

1. Open an issue for anything non-trivial, so we agree on the approach before you write code.
2. Branch from `main`: `<type>/<short-slug>` (for example `feat/outlook-connector`).
3. Small commits using [Conventional Commits](https://www.conventionalcommits.org/) with a scope:
   `feat(worker): …`, `fix(mac): …`, `feat(api): …`, `feat(db): …`, `docs: …`, `test: …`.
4. Tests with every change. All external APIs and all AI calls are faked; see `services/worker/tests/fake_google.py`.
   New tables and functions need pgTAP tests.
5. Run `scripts/check.sh <area>` (`worker`, `contracts`, `ts`, `api`, `sql`, `mac`, `security`, `terraform`, or `all`) until green.
6. Open a pull request against `main`.

## Hard rules

- **Never use real customer data**, in code, tests, fixtures, logs or issues. Use the synthetic dataset (`.example` domains).
- Logs carry ids, counts, timings and error classes only: never message bodies, quotes, email addresses or tokens.
- Ask before adding dependencies or anything that sends client data anywhere.
- Contract changes (`packages/contracts`) need `pnpm codegen` and the generated output committed; breaking changes need a new version.
- Everything in the repo is in English.

By contributing you agree that your contributions are licensed under the Apache License 2.0.
