# Local engine response fixtures

`LocalRoutes/*.json` were captured on 2026-10-05 from the actual FastAPI local routes on a temporary bundled PostgreSQL database. Synthetic seeds reuse `tests.integration.test_local_routes.served`, `test_local_client_routes.seed_client/assessment/source`, `test_local_routes_decisions._insert_card`, and the replay narrative/totals of `test_local_evidence_builders`. They contain no customer data or provider tokens.

Run `apps/mac/scripts/capture-local-fixtures.py` with the worker environment and `FIRRA_LOCAL_PG_BIN` to refresh them; this starts and removes only the synthetic fixture database. The script exercises setup domain/service/routine writes, feedback/outcomes, then captures every remaining read response. Google OAuth and disconnect transports are separately faked in `LocalRouteTests.swift`.
