"""Content-free pilot preparation; no extraction, model calls or writes."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

# docs/runbooks/design-partner-pilot.md §3: per full email, including extraction headroom.
EMAIL_COST_USD = Decimal("0.0013")
HEADROOM = Decimal("1.3")
TOTAL_KEYS = (
    "losses",
    "losses_detected_30d",
    "median_lead_days",
    "false_alarm_client_weeks_per_10_clients_per_month",
    "flicker",
)
MISS_REASONS = {"late", "coverage_changed", "low_data", "no_signal"}


def estimate_usd(emails: int) -> Decimal:
    return emails * EMAIL_COST_USD * HEADROOM


def _client(row: Mapping[str, Any]) -> str:
    prefix = str(UUID(str(row["client_id"])))[:8]
    status = row["status"] if row["status"] in {"active", "churned", "prospect"} else "unknown"
    return f"client {prefix} ({status})"


def render_estimate(rows: Sequence[Mapping[str, Any]], *, months: int) -> str:
    total = sum(int(row["emails"]) for row in rows)
    lines = [f"full-policy client-matched emails in the last {months} months: {total}"]
    lines.extend(f"{_client(row)}: {int(row['emails'])}" for row in rows)
    amount = format(estimate_usd(total), "f")
    lines += [
        f"estimate_usd = {amount}",
        "Extraction needs the owner's written go and a hard cap equal to the estimate.",
    ]
    return "\n".join(lines) + "\n"


def render_pilot_check(totals: Mapping[str, Any], clients: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for key in TOTAL_KEYS:
        value = totals.get(key)
        number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else "—"
        lines.append(f"{key}: {number}")
    for row in clients:
        reason = row.get("miss_reason")
        safe_reason = reason if isinstance(reason, str) and reason in MISS_REASONS else "none"
        lines.append(
            f"{_client(row)}: miss_reason={safe_reason}, "
            f"low_data={int(row['low_data'])}, coverage_changed={int(row['coverage_changed'])}"
        )
    return "\n".join(lines) + "\n"
