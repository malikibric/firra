"""Seed a test Slack workspace with the Northbeam Slack scenarios (L2S-14).

Posts the four scenarios from ``docs/plans/local/multichannel-signals.md``
section 8 into public channels of a test workspace, so the whole real Slack
flow (connect -> map -> sync -> extraction -> signals -> evidence with a Slack
permalink in an MCP answer) can be tested without real client chat.

All names, companies and domains are fictional (``.example``). Slack cannot
backdate posts, so seeded messages carry today's date; scenario timing that
matters (e.g. the overdue commitment in (c)) is expressed as explicit past
dates in the message text, which is what extraction reads.

Side attribution: in a single-org test workspace every seed user is firm
side. Client-side lines (complaints in (b), nudges in (c)) need a user from
a *second* test workspace invited through Slack Connect — pass their token as
``FIRRA_TEST_SLACK_CLIENT_TOKEN``. Scenario (d) needs a second firm-side
author — ``FIRRA_TEST_SLACK_AM_TOKEN``. Without those tokens the lines still
post (as the owner) so the sync/evidence E2E passes, but the dry-run report
says which signal expectations cannot fire.

Usage:
    uv run scripts/seed-test-slack/seed_test_slack.py --workspace "Firra Test Northbeam"
    uv run scripts/seed-test-slack/seed_test_slack.py --workspace "Firra Test Northbeam" --live

Docs checked (docs/research/slack-local.md, 2026-10-05): internal-app Tier 3
limits, ``chat.postMessage`` with ``thread_ts`` for replies, ``chat.getPermalink``
needs no scopes, user token sees what the user sees.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

SLACK_API = "https://slack.com/api"

OWNER = "owner"
CLIENT = "client"
AM = "am"

DUE = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")

# (key, channel, author, thread-parent-key or None, text)
PLAN: list[tuple[str, str, str, str | None, str]] = [
    # (a) Alder — benign control moving mail -> Slack: routine, positive, resolved.
    (
        "a1",
        "alder",
        OWNER,
        None,
        "Morning all — moving our weekly check-in here so the whole thread is in one place. Same "
        "agenda as the mail thread.",
    ),
    (
        "a2",
        "alder",
        CLIENT,
        "a1",
        "Sounds good, Priya here. The spring campaign numbers look "
        "strong — thanks for the fast turnaround on the creatives.",
    ),
    (
        "a3",
        "alder",
        OWNER,
        "a1",
        "Glad to hear it. Anything you need from us before Thursday's review?",
    ),
    (
        "a4",
        "alder",
        CLIENT,
        "a1",
        "Just the final spend breakdown when you have it. No rush, Thursday is fine.",
    ),
    (
        "a5",
        "alder",
        OWNER,
        None,
        "Spend breakdown for Alder & Co is in your inbox (alder.example). Shout if any line looks "
        "off.",
    ),
    ("a6", "alder", CLIENT, None, "Reviewed — all lines match our PO. Approving as is."),
    ("a7", "alder", OWNER, None, "Noted, thanks. See you Thursday."),
    ("a8", "alder", CLIENT, None, "Looking forward to it."),
    # (b) Birchfield — complaints and delays only in Slack.
    (
        "b1",
        "birch",
        CLIENT,
        None,
        "Hi team, flagging that the onboarding report is now 2 weeks late. We were promised it "
        "mid-month and our board meeting is next week.",
    ),
    (
        "b2",
        "birch",
        OWNER,
        "b1",
        "You're right, we're behind — the data export took longer than estimated. You'll have the "
        "draft by Wednesday, and I'll walk your board through it if useful.",
    ),
    (
        "b3",
        "birch",
        CLIENT,
        "b1",
        "Wednesday works, but this is the second deadline to slip this "
        "quarter. We need to talk about tracking.",
    ),
    (
        "b4",
        "birch",
        CLIENT,
        None,
        "Separate issue: the dashboard numbers you sent Friday don't match "
        "our CRM. Two conversion figures are off by a wide margin.",
    ),
    (
        "b5",
        "birch",
        OWNER,
        "b4",
        "Thanks for catching that — wrong date range on our end. Corrected "
        "sheet comes today, plus a check so it can't recur.",
    ),
    (
        "b6",
        "birch",
        CLIENT,
        None,
        "And one more: we've had three different contacts in a month and nobody seems to own our "
        "account. Every call starts with us re-explaining the goals.",
    ),
    (
        "b7",
        "birch",
        OWNER,
        "b6",
        "Fair point. Birchfield (birchfield.example) gets one owner from "
        "today, and I'll personally join the next two check-ins.",
    ),
    (
        "b8",
        "birch",
        CLIENT,
        "b6",
        "Please do. We like the work, but the communication around it has to improve or we'll "
        "reconsider the renewal.",
    ),
    (
        "b9",
        "birch",
        OWNER,
        None,
        "Understood. Summary of what we owe you is in my next message — dates included.",
    ),
    (
        "b10",
        "birch",
        OWNER,
        None,
        "Owed to Birchfield: corrected dashboard (today), onboarding "
        "draft (Wednesday), single account owner (done). I'll confirm "
        "each here as it lands.",
    ),
    # (c) Cedarline — a firm commitment made only in Slack that goes late.
    (
        "c1",
        "cedar",
        OWNER,
        None,
        "Cedarline team — I'll send the revised Q4 media plan by "
        f"{DUE}. It covers the budget split we discussed (cedarline.example).",
    ),
    (
        "c2",
        "cedar",
        CLIENT,
        None,
        "Great, the sooner the better — our finance sign-off depends on it.",
    ),
    (
        "c3",
        "cedar",
        CLIENT,
        None,
        "Hi, checking in: the media plan was due "
        f"{DUE} and we haven't seen it. Finance is asking daily now.",
    ),
    (
        "c4",
        "cedar",
        OWNER,
        "c3",
        "My fault, it slipped behind the Birchfield fixes. Sending the plan "
        "tonight plus a short note you can forward to finance.",
    ),
    (
        "c5",
        "cedar",
        CLIENT,
        "c3",
        "Tonight then. If it slips again we'll have to pause the spend until sign-off.",
    ),
    # (d) Dogwood — talked to mostly by the account manager; owner barely present.
    (
        "d1",
        "dogwood",
        AM,
        None,
        "Kicking off the Dogwood Studio thread — I'm your day-to-day contact here, will keep "
        "everything in this channel.",
    ),
    (
        "d2",
        "dogwood",
        CLIENT,
        "d1",
        "Thanks! Tom here. First ask: can we move the homepage review to Tuesday?",
    ),
    ("d3", "dogwood", AM, "d1", "Done, Tuesday 10:00. I'll bring the two homepage variants."),
    (
        "d4",
        "dogwood",
        AM,
        None,
        "Homepage variants are ready for Dogwood (dogwood.example). Variant B tests better in our "
        "preview, but bring your take tomorrow.",
    ),
    (
        "d5",
        "dogwood",
        CLIENT,
        None,
        "Variant B it is. Also: the pricing page copy feels too long — can you tighten it?",
    ),
    ("d6", "dogwood", AM, "d5", "On it, shorter draft by Friday."),
    (
        "d7",
        "dogwood",
        AM,
        None,
        "Shorter pricing draft is in. Also flagged: trial signups dipped "
        "this week, worth a look on your end.",
    ),
    (
        "d8",
        "dogwood",
        CLIENT,
        None,
        "Saw the dip too, it's seasonal for us. Draft looks much better, approved.",
    ),
    ("d9", "dogwood", AM, None, "Noted, thanks. Next check-in same time next week?"),
    ("d10", "dogwood", CLIENT, None, "Works for us."),
    ("d11", "dogwood", AM, None, "Locked in. Anything urgent before then, ping me here directly."),
    (
        "d12",
        "dogwood",
        OWNER,
        None,
        "Popping in briefly — I'm the founder, Sam runs your account "
        "day to day. Happy with how things are going?",
    ),
    ("d13", "dogwood", CLIENT, "d12", "All good here, Sam's been great."),
]

CHANNELS = ("alder", "birch", "cedar", "dogwood")


@dataclass
class SeedClient:
    client: httpx.Client
    token: str
    pacer_interval: float = 1.2
    _next: float = field(default=0.0, init=False)

    def _pace(self) -> None:
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
            now = self._next
        self._next = now + self.pacer_interval

    def call(self, method: str, **params: object) -> dict:
        """One Slack Web API call with pacing and 429/backoff retries."""
        delay = 1.0
        for _ in range(6):
            self._pace()
            response = self.client.post(
                f"{SLACK_API}/{method}",
                headers={"Authorization": f"Bearer {self.token}"},
                json=params,
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("ok"):
                return body
            error = str(body.get("error", "unknown"))
            if error == "ratelimited":
                wait = response.headers.get("Retry-After")
                time.sleep(float(wait) if wait else delay)
                delay = min(delay * 2, 16.0)
                continue
            raise RuntimeError(f"slack {method} failed: {error}")
        raise RuntimeError(f"slack {method}: retry loop exhausted")


def check_workspace(client: httpx.Client, token: str, expected: str) -> tuple[str, str]:
    """Team name and user id for a token; refuse unless the team matches
    (never touch real workspaces)."""
    response = client.post(
        f"{SLACK_API}/auth.test",
        headers={"Authorization": f"Bearer {token}"},
        json={},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise SystemExit(f"auth.test failed: {body.get('error')}")
    team = str(body.get("team", ""))
    if team != expected:
        raise SystemExit(f"refusing: token is for workspace {team!r}, expected {expected!r}")
    return team, str(body.get("user_id", ""))


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"channels": {}, "messages": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))


def channel_name(prefix: str, short: str) -> str:
    return f"{prefix}-{short}"


def dry_run_report(prefix: str, tokens: dict[str, str]) -> str:
    lines = []
    for short in CHANNELS:
        own = sum(1 for _, c, a, _, _ in PLAN if c == short and a == OWNER)
        cli = sum(1 for _, c, a, _, _ in PLAN if c == short and a == CLIENT)
        am = sum(1 for _, c, a, _, _ in PLAN if c == short and a == AM)
        lines.append(
            f"{channel_name(prefix, short)}: messages={own + cli + am} "
            f"owner={own} client={cli} am={am}"
        )
    warnings = []
    if not tokens.get(CLIENT):
        warnings.append(
            "no FIRRA_TEST_SLACK_CLIENT_TOKEN: client-side lines post as owner; "
            "complaint/commitment signals that need client authorship will not fire"
        )
    if not tokens.get(AM):
        warnings.append(
            "no FIRRA_TEST_SLACK_AM_TOKEN: scenario (d) posts as owner, "
            "owner share stays high and participant_coverage will not trigger"
        )
    report = f"channels={len(CHANNELS)} messages={len(PLAN)} due={DUE}\n" + "\n".join(lines)
    if warnings:
        report += "\nwarnings:\n- " + "\n- ".join(warnings)
    return report


def ensure_channels(seed: SeedClient, prefix: str, state: dict) -> dict[str, str]:
    """Create (or reuse) one public channel per scenario; returns {short: id}."""
    listed: dict[str, str] = {}
    cursor: str | None = None
    while True:
        params: dict[str, object] = {"types": "public_channel", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        body = seed.call("conversations.list", **params)
        for channel in body.get("channels", []):
            listed[str(channel["name"])] = str(channel["id"])
        cursor = str(body.get("response_metadata", {}).get("next_cursor", ""))
        if not cursor:
            break
    ids: dict[str, str] = {}
    for short in CHANNELS:
        name = channel_name(prefix, short)
        if name in state.get("channels", {}):
            ids[short] = state["channels"][name]
            continue
        if name in listed:
            ids[short] = listed[name]
        else:
            created = seed.call("conversations.create", name=name, is_private=False)
            ids[short] = str(created["channel"]["id"])
        state.setdefault("channels", {})[name] = ids[short]
    return ids


def seed(seed_clients: dict[str, SeedClient], prefix: str, state: dict) -> dict[str, int]:
    ids = ensure_channels(seed_clients[OWNER], prefix, state)
    done: dict = state.setdefault("messages", {})
    added = {"messages": 0, "skipped": 0}
    ts_by_key: dict[str, str] = {k: v["ts"] for k, v in done.items() if isinstance(v, dict)}
    for key, short, author, parent, text in PLAN:
        if key in done:
            added["skipped"] += 1
            continue
        seed_client = seed_clients.get(author) or seed_clients[OWNER]
        channel_id = ids[short]
        try:
            seed_client.call("conversations.join", channel=channel_id)
        except RuntimeError as exc:
            if "already_in_channel" not in str(exc):
                raise
        params: dict[str, object] = {"channel": channel_id, "text": text}
        if parent:
            params["thread_ts"] = ts_by_key[parent]
        posted = seed_client.call("chat.postMessage", **params)
        ts = str(posted["ts"])
        link = seed_clients[OWNER].call("chat.getPermalink", channel=channel_id, message_ts=ts)
        done[key] = {
            "ts": ts,
            "channel": channel_id,
            "permalink": str(link.get("permalink", "")),
            "posted_as": author if author in seed_clients else OWNER,
        }
        ts_by_key[key] = ts
        added["messages"] += 1
    return added


def verify(seed: SeedClient, prefix: str, state: dict) -> str:
    if not state.get("channels"):
        return "no seed state: nothing to verify (run with --live first)"
    parts = []
    for short in CHANNELS:
        name = channel_name(prefix, short)
        channel_id = state.get("channels", {}).get(name, "")
        if not channel_id:
            parts.append(f"{name}: missing")
            continue
        body = seed.call("conversations.history", channel=channel_id, limit=100)
        messages = [m for m in body.get("messages", []) if not m.get("subtype")]
        planned = sum(1 for _, c, _, _, _ in PLAN if c == short)
        seeded = sum(
            1
            for k, v in state.get("messages", {}).items()
            if isinstance(v, dict) and v.get("channel") == channel_id
        )
        parts.append(f"{name}: history={len(messages)} seeded={seeded} planned={planned}")
    first = next((v for v in state.get("messages", {}).values() if isinstance(v, dict)), {})
    if first.get("permalink"):
        parts.append(f"permalink_sample={first['permalink']}")
    return "\n".join(parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post the Northbeam Slack scenarios into a test workspace. "
        "Dry-run unless --live."
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Expected test workspace, e.g. 'Firra Test Northbeam'",
    )
    parser.add_argument("--channel-prefix", default="firra-seed")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually post; without it, print the plan only",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Compare seed channels with the plan, post nothing",
    )
    parser.add_argument(
        "--state",
        default=str(Path.home() / ".cache" / "firra-seed-test-slack-state.json"),
    )
    parser.add_argument("--reset-state", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tokens = {
        OWNER: os.environ.get("FIRRA_TEST_SLACK_TOKEN", ""),
        CLIENT: os.environ.get("FIRRA_TEST_SLACK_CLIENT_TOKEN", ""),
        AM: os.environ.get("FIRRA_TEST_SLACK_AM_TOKEN", ""),
    }
    if not args.live and not args.verify_only:
        sys.stdout.write(dry_run_report(args.channel_prefix, tokens) + "\n")
        sys.stdout.write("re-run with --live to post\n")
        return 0
    if not tokens[OWNER]:
        sys.stderr.write("missing FIRRA_TEST_SLACK_TOKEN\n")
        return 2
    with httpx.Client() as http:
        check_workspace(http, tokens[OWNER], args.workspace)
        seed_clients = {OWNER: SeedClient(http, tokens[OWNER])}
        for role in (CLIENT, AM):
            if tokens[role]:
                check_workspace(http, tokens[role], args.workspace)
                seed_clients[role] = SeedClient(http, tokens[role])
        if args.verify_only:
            state = load_state(Path(args.state))
            sys.stdout.write(verify(seed_clients[OWNER], args.channel_prefix, state) + "\n")
            return 0
        state_path = Path(args.state)
        state = {} if args.reset_state else load_state(state_path)
        try:
            added = seed(seed_clients, args.channel_prefix, state)
        finally:
            save_state(state_path, state)
        sys.stdout.write(f"posted={added['messages']} skipped={added['skipped']}\n")
        sys.stdout.write(verify(seed_clients[OWNER], args.channel_prefix, state) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
