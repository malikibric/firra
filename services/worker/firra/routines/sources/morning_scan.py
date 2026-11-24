"""Stored changes since the previous successful morning scan, never mail text."""

from datetime import date

from firra.assessment.config import risk_rank
from firra.db.routine_changes import RoutineChangesRepo
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.config import load_config
from firra.routines.model import Candidate, Trigger

EXPANSION = ("none", "hypothesis", "strong")
SEVERITY = ("low", "medium", "high")


class MorningScan:
    def candidates(
        self, repo: RoutinesRepo, *, workspace_id: WorkspaceId, as_of: date
    ) -> list[Candidate]:
        changes = RoutineChangesRepo(repo.pool)
        since = changes.since(workspace_id=workspace_id, as_of=as_of)
        result = []
        for row in changes.assessments(workspace_id=workspace_id, since=since, as_of=as_of):
            common = {
                "client_id": row["client_id"],
                "as_of": as_of,
                "evidence_ids": frozenset(row["evidence_ids"]),
            }
            previous = row["previous_risk_state"]
            risk = row["risk_state"]
            if previous is not None and previous != risk:
                result.append(
                    Candidate(
                        **common,
                        kind="state_change",
                        risk_to=risk,
                        trigger="risk_up" if risk_rank(risk) > risk_rank(previous) else "risk_down",
                    )
                )
            expansion = row["expansion_state"]
            previous_expansion = row["previous_expansion"]
            if previous_expansion is not None and EXPANSION.index(expansion) > EXPANSION.index(
                previous_expansion
            ):
                result.append(
                    Candidate(
                        **common, kind="expansion", trigger="expansion_up", expansion_to=expansion
                    )
                )
        for row in changes.signals(workspace_id=workspace_id, since=since, as_of=as_of):
            trigger: Trigger
            previous_severity = row["previous_severity"]
            change = row["change"]
            if change in ("opened", "reopened") or (
                change == "severity_changed"
                and previous_severity is not None
                and SEVERITY.index(row["severity"]) > SEVERITY.index(previous_severity)
            ):
                trigger = "signal_opened"
            elif change == "resolved":
                trigger = "signal_resolved"
            else:
                continue
            result.append(
                Candidate(
                    row["client_id"],
                    "state_change",
                    trigger,
                    as_of,
                    severity=row["severity"],
                    evidence_ids=frozenset(row["evidence_ids"]),
                )
            )
        cfg = load_config()
        for row in changes.deadlines(workspace_id=workspace_id):
            days_now = (row["deadline"] - as_of).days
            days_prev = (row["deadline"] - since).days
            if days_now >= 0 and any(
                days_prev > threshold >= days_now
                for threshold in (cfg.notice_near_days, cfg.notice_close_days)
            ):
                result.append(Candidate(row["client_id"], "state_change", "notice_close", as_of))
        return result
