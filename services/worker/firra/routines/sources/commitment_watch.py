"""Commitments that became late since the previous successful watch."""

from datetime import date

from firra.db.routine_commitments import RoutineCommitmentsRepo
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.memory.rules import commitment_status
from firra.routines.model import Candidate


class CommitmentWatch:
    def candidates(
        self, repo: RoutinesRepo, *, workspace_id: WorkspaceId, as_of: date
    ) -> list[Candidate]:
        commitments = RoutineCommitmentsRepo(repo.pool)
        since = commitments.since(workspace_id=workspace_id, as_of=as_of)
        return [
            Candidate(
                client_id=row["client_id"],
                kind="commitment_watch",
                trigger="commitment_late",
                owner_side=row["owner_side"],
                evidence_ids=frozenset(row["evidence_ids"]),
                as_of=as_of,
            )
            for row in commitments.commitments(workspace_id=workspace_id, as_of=as_of)
            if commitment_status(row["status"], row["due_date"], as_of) == "late"
            and commitment_status(row["status"], row["due_date"], since) != "late"
        ]
