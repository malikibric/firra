"""The eval collector must never mix rows from two local workspaces."""

from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.db.evals import EvalRepo
from firra.db.pool import create_pool_for
from firra.db.types import WorkspaceId
from tests.conftest import local_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def pair() -> Iterator[tuple[UUID, UUID, UUID, UUID]]:
    workspaces = (uuid4(), uuid4())
    evidence_ids: list[UUID] = []
    db_url = local_settings().database_url.get_secret_value()
    with psycopg.connect(db_url, row_factory=dict_row, autocommit=True) as conn:
        for index, workspace_id in enumerate(workspaces):
            conn.execute(
                "insert into public.workspace (id, name) values (%s, %s)",
                (workspace_id, f"Eval fixture {index}"),
            )
            client = conn.execute(
                "insert into public.client (workspace_id, name, policy) "
                "values (%s, %s, 'full') returning id",
                (workspace_id, f"Client {index}"),
            ).fetchone()
            connection = conn.execute(
                "insert into public.connection (workspace_id, provider, mode, account_label) "
                "values (%s, 'email_upload', 'upload', 'fixture') returning id",
                (workspace_id,),
            ).fetchone()
            assert client and connection
            quote = f"Workspace {index} owns this quote."
            record = conn.execute(
                "insert into public.source_record "
                "(workspace_id, connection_id, provider, external_id, kind, client_id, "
                "policy_at_ingest, occurred_at, direction, body_clean) "
                "values (%s, %s, 'email_upload', 'shared-id', 'email', %s, 'full', now(), "
                "'in', %s) returning id",
                (workspace_id, connection["id"], client["id"], quote),
            ).fetchone()
            assert record
            evidence = conn.execute(
                "insert into public.evidence "
                "(workspace_id, source_record_id, client_id, quote, locator) "
                "values (%s, %s, %s, %s, %s) returning id",
                (
                    workspace_id,
                    record["id"],
                    client["id"],
                    quote,
                    Jsonb({"start": 0, "end": len(quote)}),
                ),
            ).fetchone()
            assert evidence
            evidence_ids.append(evidence["id"])
            with conn.transaction():
                extraction = conn.execute(
                    "insert into public.extraction "
                    "(workspace_id, source_record_id, client_id, kind, payload, model, "
                    "prompt_version, processing_version, item_index) "
                    "values (%s, %s, %s, 'complaint', %s, 'fake', 'extract.v1', 1, 0) "
                    "returning id",
                    (workspace_id, record["id"], client["id"], Jsonb({"subtype": "quality"})),
                ).fetchone()
                assert extraction
                conn.execute(
                    "insert into public.extraction_evidence "
                    "(workspace_id, extraction_id, evidence_id, client_id) values (%s, %s, %s, %s)",
                    (workspace_id, extraction["id"], evidence["id"], client["id"]),
                )
            conn.execute(
                "insert into public.job (workspace_id, type, status, idempotency_key, progress) "
                "values (%s, 'process.records', 'succeeded', %s, %s)",
                (
                    workspace_id,
                    f"eval-{index}",
                    Jsonb({"items": 1, "dropped": {"quote_not_found": index}}),
                ),
            )
            conn.execute(
                "insert into public.llm_usage "
                "(workspace_id, model, input_tokens, output_tokens, cost_usd) "
                "values (%s, 'fake', %s, 1, %s)",
                (workspace_id, index + 1, Decimal("0.01") * (index + 1)),
            )
        try:
            yield workspaces[0], workspaces[1], evidence_ids[0], evidence_ids[1]
        finally:
            for workspace_id in workspaces:
                conn.execute("delete from public.workspace where id = %s", (workspace_id,))


def test_collector_is_workspace_scoped(pair: tuple[UUID, UUID, UUID, UUID]) -> None:
    first, second, first_evidence, second_evidence = pair
    with create_pool_for(local_settings().database_url.get_secret_value()) as pool:
        inputs = EvalRepo(pool, WorkspaceId(first)).collect()
        other = EvalRepo(pool, WorkspaceId(second)).collect()
    assert [row.evidence_id for row in inputs.evidence] == [first_evidence]
    assert [row.evidence_id for row in other.evidence] == [second_evidence]
    assert inputs.evidence[0].external_id == other.evidence[0].external_id == "shared-id"
    assert inputs.items[0].quote != other.items[0].quote
    assert inputs.verify_dropped == 0 and inputs.items_attempted == 1
    assert other.verify_dropped == 1 and other.items_attempted == 2
    assert inputs.usage[0].input_tokens == 1 and other.usage[0].input_tokens == 2
