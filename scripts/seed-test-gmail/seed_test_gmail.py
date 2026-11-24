#!/usr/bin/env python3
"""Seed a test Gmail + Calendar account with the Northbeam corpus (L2-04).

Reads ``services/worker/synthetic/northbeam/corpus/{messages,events}.jsonl``,
builds RFC 822 mail with original dates, and imports it into a test Google
account the owner created. The account plays the founder's mailbox: firm-side
addresses are rewritten to the account address, client addresses on fictional
domains are kept. Calendar events keep original start times and attendees.

Gmail date behaviour (mirrors ``docs/research/google-local.md`` Q7, read
2026-10-04): ``users.messages.import`` with
``internalDateSource=dateHeader`` keeps the ``Date`` header, so original
dates survive. If ``import`` ever stops accepting our scopes, ``--method
insert`` falls back to ``users.messages.insert`` (same ``dateHeader``
semantics for the internal date); if neither can keep dates, the documented
fallback is IMAP ``APPEND`` with ``INTERNALDATE`` (see README, risk R6).

Usage:
    uv run scripts/seed-test-gmail/seed_test_gmail.py --dry-run
    uv run scripts/seed-test-gmail/seed_test_gmail.py --account firra.test.northbeam@gmail.com
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "services" / "worker" / "synthetic" / "northbeam" / "corpus"
MESSAGES_FILE = CORPUS_DIR / "messages.jsonl"
EVENTS_FILE = CORPUS_DIR / "events.jsonl"

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/primary"
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint, not a secret
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
]
# If messages.import answers 403 insufficientPermissions for gmail.insert,
# rerun with this scope set (see README). gmail.modify covers import+insert.
FALLBACK_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
]

FIRM_DOMAIN = "northbeam.example"
SEED_TAG = "firraSeedL204"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def is_firm(email: str) -> bool:
    return email.lower().endswith("@" + FIRM_DOMAIN)


def fmt_addr(name: str, email: str) -> str:
    name = (name or "").replace("\r", " ").replace("\n", " ")
    return f"{name} <{email}>" if name else email


def rewrite(email: str, name: str, account: str) -> tuple[str, str]:
    """Firm-side addresses become the test account; client addresses are kept."""
    if is_firm(email):
        return account, name
    return email, name


def thread_refs(messages: list[dict]) -> dict[str, dict[str, str | None]]:
    """Map external_id -> {"in_reply_to": msg-id, "references": msg-ids}.

    The corpus carries thread_key but no parent links; order by occurred_at and
    chain each thread so seeded mail threads like the original conversation.
    """
    by_thread: dict[str, list[dict]] = {}
    for message in messages:
        by_thread.setdefault(message["thread_key"], []).append(message)
    refs: dict[str, dict[str, str | None]] = {}
    for thread in by_thread.values():
        ordered = sorted(thread, key=lambda m: (m["occurred_at"], m["external_id"]))
        chain: list[str] = []
        for message in ordered:
            parent = chain[-1] if chain else None
            refs[message["external_id"]] = {
                "in_reply_to": parent,
                "references": " ".join(chain) or None,
            }
            chain.append(message["message_id_header"])
    return refs


def build_rfc822(message: dict, account: str, refs: dict[str, dict[str, str | None]]) -> bytes:
    """RFC 822 bytes with original From/To/Cc/Subject/Date/Message-ID/threading."""
    sender_email, sender_name = rewrite(
        message["sender"]["email"], message["sender"].get("name", ""), account
    )
    to = ", ".join(
        fmt_addr(*rewrite(p["email"], p.get("name", ""), account)[::-1])
        for p in message.get("to", [])
    )
    cc_list = [
        fmt_addr(*rewrite(p["email"], p.get("name", ""), account)[::-1])
        for p in message.get("cc", [])
    ]
    occurred = datetime.fromisoformat(message["occurred_at"].replace("Z", "+00:00"))
    lines = [
        f"From: {fmt_addr(sender_name, sender_email)}",
        f"To: {to}",
        *([f"Cc: {', '.join(cc_list)}"] if cc_list else []),
        f"Subject: {message.get('subject', '(no subject)')}",
        f"Date: {format_datetime(occurred)}",
        f"Message-ID: {message['message_id_header']}",
    ]
    ref = refs.get(message["external_id"], {})
    if ref.get("in_reply_to"):
        lines.append(f"In-Reply-To: {ref['in_reply_to']}")
    if ref.get("references"):
        lines.append(f"References: {ref['references']}")
    lines += ["MIME-Version: 1.0", 'Content-Type: text/plain; charset="utf-8"', ""]
    return ("\r\n".join(lines) + message.get("body_text", "") + "\r\n").encode("utf-8")


def encode_raw(rfc822: bytes) -> str:
    return base64.urlsafe_b64encode(rfc822).decode().rstrip("=")


def calendar_event_id(external_id: str) -> str:
    """Deterministic Calendar id (API ids allow [a-z0-9]; corpus ids contain dashes)."""
    return "seed" + hashlib.sha256(external_id.encode()).hexdigest()[:28]


@dataclass
class SeedClient:
    client: httpx.Client
    token: str
    pacer_interval: float = 0.5
    _next: float = field(default=0.0, init=False)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _pace(self) -> None:
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
            now = self._next
        self._next = now + self.pacer_interval

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        attempts, delay = 6, 1.0
        for _ in range(attempts):
            self._pace()
            response = self.client.request(method, url, headers=self._headers(), **kwargs)  # type: ignore[arg-type]
            if response.status_code in (429,) or (
                response.status_code == 403 and _throttled(response)
            ):
                wait = _retry_after(response) or delay
                time.sleep(wait)
                delay = min(delay * 2, 16.0)
                continue
            return response
        raise RuntimeError("request retry loop exited without a response")

    def message_exists(self, message_id_header: str) -> bool:
        response = self._request(
            "GET", f"{GMAIL_API}/messages", params={"q": f"rfc822msgid:{message_id_header}"}
        )
        response.raise_for_status()
        return bool(response.json().get("messages"))

    def import_message(self, raw_b64: str, *, outbound: bool, method: str) -> None:
        label_ids = ["SENT"] if outbound else ["INBOX"]
        if method == "import":
            response = self._request(
                "POST",
                f"{GMAIL_API}/messages/import",
                params={"internalDateSource": "dateHeader"},
                json={"message": {"raw": raw_b64, "labelIds": label_ids}},
            )
        else:
            response = self._request(
                "POST",
                f"{GMAIL_API}/messages",
                params={"internalDateSource": "dateHeader"},
                json={"raw": raw_b64, "labelIds": label_ids},
            )
        response.raise_for_status()

    def event_exists(self, event_id: str) -> bool:
        response = self._request("GET", f"{CALENDAR_API}/events/{event_id}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def insert_event(self, event: dict, account: str) -> None:
        event_id = calendar_event_id(event["external_id"])
        attendees = [
            {"email": a["email"], "displayName": a.get("name", "")}
            for a in event.get("attendees", [])
        ]
        body = {
            "id": event_id,
            "summary": event.get("title", "(no title)"),
            "start": {"dateTime": event["starts_at"]},
            "end": {"dateTime": event.get("ends_at", event["starts_at"])},
            "attendees": attendees,
            "extendedProperties": {
                "private": {"firraSeed": SEED_TAG, "externalId": event["external_id"]}
            },
        }
        response = self._request("POST", f"{CALENDAR_API}/events", json=body)
        response.raise_for_status()
        if event.get("status") == "cancelled":
            response = self._request(
                "PATCH", f"{CALENDAR_API}/events/{event_id}", json={"status": "cancelled"}
            )
            response.raise_for_status()

    def list_message_dates(self) -> list[str]:
        """All internalDates in the account (verification mode)."""
        dates: list[str] = []
        page: str | None = None
        while True:
            params: dict[str, object] = {"maxResults": 500}
            if page:
                params["pageToken"] = page
            response = self._request("GET", f"{GMAIL_API}/messages", params=params)
            response.raise_for_status()
            body = response.json()
            for item in body.get("messages", []):
                detail = self._request(
                    "GET", f"{GMAIL_API}/messages/{item['id']}", params={"format": "minimal"}
                )
                detail.raise_for_status()
                dates.append(detail.json().get("internalDate", "0"))
            page = body.get("nextPageToken")
            if not page:
                return dates


def _throttled(response: httpx.Response) -> bool:
    try:
        errors = response.json().get("error", {}).get("errors", [])
    except ValueError:
        return False
    return any(e.get("reason") in {"rateLimitExceeded", "userRateLimitExceeded"} for e in errors)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def check_account(
    client: httpx.Client, token: str, *, account: str, allow_domain: str | None
) -> str:
    """Signed-in address; refuse unless it matches the allow-list (never touch real mail)."""
    response = client.get(USERINFO, headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    email = str(response.json().get("email", ""))
    allowed = email == account or (allow_domain and email.endswith("@" + allow_domain))
    if not allowed:
        raise SystemExit(f"refusing: signed in as {email}, expected {account}")
    return email


def desktop_login(client_id: str, client_secret: str, scopes: list[str]) -> str:
    """Desktop OAuth loopback flow with PKCE; token stays in memory only."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    code_holder: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"] = query.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>Signed in. Close this tab and return to Firra.</p>")

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    params = {
        "client_id": client_id,
        "redirect_uri": f"http://127.0.0.1:{port}",
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = OAUTH_AUTH_URL + "?" + urllib.parse.urlencode(params)
    sys.stdout.write(f"Open this URL in the system browser:\n{url}\n")
    with contextlib.suppress(Exception):
        webbrowser.open(url)
    server.handle_request()
    server.server_close()
    if "code" not in code_holder:
        raise SystemExit("no authorization code received")
    token = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code_holder["code"],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": f"http://127.0.0.1:{port}",
        },
        timeout=30,
    )
    token.raise_for_status()
    return str(token.json()["access_token"])


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"messages": [], "events": []}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))


def dry_run_report(messages: list[dict], events: list[dict]) -> str:
    dates = sorted(m["occurred_at"] for m in messages)
    firm = sum(1 for m in messages if is_firm(m["sender"]["email"]))
    return (
        f"messages={len(messages)} threads={len({m['thread_key'] for m in messages})} "
        f"inbound={len(messages) - firm} outbound={firm} events={len(events)} "
        f"earliest={dates[0]} latest={dates[-1]}"
    )


def seed(
    seed_client: SeedClient,
    messages: list[dict],
    events: list[dict],
    account: str,
    state: dict,
    *,
    method: str,
) -> dict[str, int]:
    refs = thread_refs(messages)
    done_messages = set(state.get("messages", []))
    done_events = set(state.get("events", []))
    added = {"messages": 0, "skipped": 0, "events": 0, "events_skipped": 0}
    for message in sorted(messages, key=lambda m: (m["occurred_at"], m["external_id"])):
        if message["external_id"] in done_messages:
            added["skipped"] += 1
            continue
        if seed_client.message_exists(message["message_id_header"]):
            done_messages.add(message["external_id"])
            added["skipped"] += 1
            continue
        raw = build_rfc822(message, account, refs)
        seed_client.import_message(
            encode_raw(raw), outbound=is_firm(message["sender"]["email"]), method=method
        )
        done_messages.add(message["external_id"])
        added["messages"] += 1
    for event in sorted(events, key=lambda e: (e["starts_at"], e["external_id"])):
        if event["external_id"] in done_events:
            added["events_skipped"] += 1
            continue
        if seed_client.event_exists(calendar_event_id(event["external_id"])):
            done_events.add(event["external_id"])
            added["events_skipped"] += 1
            continue
        seed_client.insert_event(event, account)
        done_events.add(event["external_id"])
        added["events"] += 1
    state["messages"], state["events"] = sorted(done_messages), sorted(done_events)
    return added


def verify(seed_client: SeedClient, messages: list[dict]) -> str:
    dates = sorted(int(d) for d in seed_client.list_message_dates())
    earliest = datetime.fromtimestamp(dates[0] / 1000, UTC).isoformat() if dates else "none"
    latest = datetime.fromtimestamp(dates[-1] / 1000, UTC).isoformat() if dates else "none"
    corpus_dates = sorted(m["occurred_at"] for m in messages)
    return (
        f"account_messages={len(dates)} corpus_messages={len(messages)} "
        f"account_earliest={earliest} account_latest={latest} "
        f"corpus_earliest={corpus_dates[0]} corpus_latest={corpus_dates[-1]}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed the Northbeam corpus into a test Gmail account."
    )
    parser.add_argument(
        "--account", required=True, help="Test account address, e.g. firra.test.northbeam@gmail.com"
    )
    parser.add_argument(
        "--allow-domain", default=None, help="Also accept signed-in addresses at this domain"
    )
    parser.add_argument("--method", choices=["import", "insert"], default="import")
    parser.add_argument(
        "--use-fallback-scopes",
        action="store_true",
        help="Request gmail.modify instead of gmail.insert",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Build everything, print counts, no network"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Compare account mail with the corpus, seed nothing",
    )
    parser.add_argument(
        "--state", default=str(Path.home() / ".cache" / "firra-seed-test-gmail-state.json")
    )
    parser.add_argument("--reset-state", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    messages = read_jsonl(MESSAGES_FILE)
    events = read_jsonl(EVENTS_FILE)
    if args.dry_run:
        sys.stdout.write(dry_run_report(messages, events) + "\n")
        return 0
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        sys.stderr.write("missing GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET\n")
        return 2
    scopes = FALLBACK_SCOPES if args.use_fallback_scopes else DEFAULT_SCOPES
    with httpx.Client(timeout=30) as http:
        token = desktop_login(client_id, client_secret, scopes)
        check_account(http, token, account=args.account, allow_domain=args.allow_domain)
        seed_client = SeedClient(http, token)
        if args.verify_only:
            sys.stdout.write(verify(seed_client, messages) + "\n")
            return 0
        state_path = Path(args.state)
        state = {"messages": [], "events": []} if args.reset_state else load_state(state_path)
        try:
            added = seed(seed_client, messages, events, args.account, state, method=args.method)
        finally:
            save_state(state_path, state)
        sys.stdout.write(
            f"imported_messages={added['messages']} skipped={added['skipped']} "
            f"events={added['events']} events_skipped={added['events_skipped']}\n"
        )
        sys.stdout.write(verify(seed_client, messages) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
