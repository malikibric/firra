"""Coverage guard (P2b D4) and participant coverage (signals.v3).

A connection that stopped syncing, or an owner who is not on most of the firm's threads with the
client, is never read as the client going quiet: volume rules are suppressed and a caveat says why.
"""

from collections.abc import Iterable, Mapping
from datetime import timedelta

from firra.assessment.model import Caveat
from firra.signals.config import PartialViewConfig, load_config
from firra.signals.facts import ClientFacts
from firra.signals.model import SignalDraft
from firra.signals.rules import RULES
from firra.signals.rules.base import RuleSpec, in_window


def volume_rule_types(rules: Mapping[str, RuleSpec] = RULES) -> frozenset[str]:
    """Rules that count email volume (`uses_volume`); only these are suppressed."""
    return frozenset(name for name, spec in rules.items() if spec.uses_volume)


def partial_view(facts: ClientFacts, cfg: PartialViewConfig) -> bool:
    """Spec section 5: others at the firm talk to this client in threads the owner is not on."""
    if not facts.owner_person_ids:
        return False
    firm = [
        r
        for r in facts.records
        if r.author_side == "firm"
        and r.occurred_at <= facts.ref
        and in_window(r.occurred_at, facts.ref, newer_than_days=cfg.window_days)
    ]
    if not firm:
        return False
    others = {
        r.author_person_id
        for r in firm
        if r.author_person_id is not None and r.author_person_id not in facts.owner_person_ids
    }
    owned = sum(r.author_person_id in facts.owner_person_ids for r in firm)
    return len(others) >= cfg.min_other_firm_people and owned / len(firm) < cfg.max_owner_share


def coverage_guard(
    facts: ClientFacts,
    drafts: Iterable[SignalDraft],
    volume_types: frozenset[str],
    *,
    tolerance: timedelta,
    partial: PartialViewConfig | None = None,
) -> tuple[list[SignalDraft], tuple[Caveat, ...]]:
    """Drops volume drafts when a baseline connection's data ends before ``ref``.

    Coverage is broken when any connection that carried the client's baseline records has a
    ``synced_until`` more than ``tolerance`` before ``ref`` (revoked, errored, or an upload that
    ends earlier); a few days' lag is normal and not a gap. Suppressed
    drafts are not stored; the caveat names the connections and the suppressed rule types.
    """
    drafts = list(drafts)
    caveats: list[Caveat] = []
    broken = sorted(
        str(c.connection_id)
        for c in facts.coverage
        if c.synced_until is not None and c.synced_until < facts.ref - tolerance
    )
    suppressed = sorted({d.type for d in drafts if d.type in volume_types})
    if broken:
        caveats.append(
            Caveat(
                code="coverage_changed",
                detail=(
                    "A connection that carried this client's mail or chat stopped syncing before "
                    "this date, so changes in message volume are not counted."
                ),
                data={"connection_ids": broken, "suppressed_types": suppressed},
            )
        )
    blind = partial_view(facts, partial or load_config().partial_view)
    if blind:
        caveats.append(
            Caveat(
                code="participant_coverage",
                detail=(
                    "Firra sees only your mailbox and Slack; other people at your firm also talk "
                    "to this client."
                ),
                data={"suppressed_types": suppressed},
            )
        )
    if not (broken or blind):
        return drafts, ()
    return [d for d in drafts if d.type not in volume_types], tuple(caveats)
