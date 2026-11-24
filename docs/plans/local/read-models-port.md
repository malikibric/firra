# Porting the `/v1` read models and writes to the local engine (guide for L4-02a, b, c)

The TypeScript API (`apps/api`) served the Mac app and MCP from Supabase as the **caller**: every query ran with the user's token, so Postgres row-level security (RLS) decided what a user could see, and
writes went through audited `SECURITY DEFINER` RPCs. Local v1 keeps exactly that model on the bundled Postgres; only the language of the glue changes.

## The pattern (read `firra/local/routes/routines.py`, `me.py`, `common.py` and `firra/local/readctx.py` first)

1. **Caller session.** `ReadContext.caller()` opens a transaction that runs as role `authenticated` with the owner's id in `request.jwt.claim.sub`. Run every read and every RPC inside it. RLS then hides `excluded` clients
   and policy-protected rows exactly as before. **Never read client data as superuser and never re-implement a policy filter in Python**; test that RLS does the job instead (excluded client absent, `metadata_only` client without quotes).
2. **SQL lives in `firra/db/`** (SQL guard): one module per area (for example `firra/db/local_clients.py`), every statement workspace-scoped. Functions take `conn` and return plain dict rows.
3. **Builders are pure Python** ported 1:1 from the TypeScript `lib/*.ts` builders (`attention.ts`, `changes.ts`, `clients.ts`, `meetings.ts`, `read-page.ts`, …). Keep names, ordering rules, limits, pagination cursors and rounding identical; the
   TS unit tests of a builder are your oracle: port them to pytest with the same inputs and expected outputs.
4. **Routes** go in `firra/local/routes/<area>.py` with `build_router(ctx)` and are listed in `firra/local/routes/__init__.py`. Paths and verbs are **unchanged** (`/v1/workspaces/{workspace_id}/…`). The workspace path parameter is guarded by
   `workspace_guard` (only the one local workspace exists). Request and response bodies are the **generated Pydantic models** in `firra_contracts.generated_v1` (generated from the zod contracts; do not hand-write a parallel model, and do not edit the
   generated files). Validate the response model before returning; `model_dump(mode="json")`.
5. **Audited reads** call `audit_read(ctx, "read.<name>", count=…, target_type=…, target_id=…)` **before** returning data (same action names as `apps/api/src/routes/read.ts`); an audit failure returns nothing (500).
6. **Errors** use `ApiError(status, code, message)` with the codes of the TS routes (`not_found` 404 where TS answered 404, `forbidden` 403, `invalid_request` 422). Path ids are UUIDs (FastAPI validates; the handler in `server.py` shapes the 422).
7. **Writes** call the same RPCs the TS write store called (`apps/api/src/lib/write-store.ts`, `setup-store.ts`) inside `ctx.caller()`; map `42501` (insufficient privilege) to 404 where TS did.
8. **Logs** carry route name, status, duration, counts; never ids of mail, quotes, names or addresses.

## Tests (the TS integration tests are the oracle)

For every route port the matching test file under `apps/api/test/integration/` (`read.test.ts`, `clients.test.ts`, `changes.test.ts`, `timeline-evidence.test.ts`, `replay.test.ts`, `decisions.test.ts`, `setup.test.ts`, `routines*.test.ts`) to a Python
integration test in `services/worker/tests/integration/test_local_routes_<area>.py` (marker `integration`, bundled database, `TestClient` against `create_local_app(routers=all_routers(ctx))`, see `test_local_routes.py`). Seed with `psycopg` as superuser
(the TS tests used the service role); keep every assertion, including the isolation ones (a second workspace's data is invisible: create a second workspace as another user) and the policy ones. A route is done when each assertion of its TS test has a
Python twin that passes. Differences forced by the new setting (no contributors, no members, one workspace) are listed in the Report, not silently dropped.

## Definition of done for each part

`scripts/check.sh worker` green, the new integration tests green with `FIRRA_LOCAL_PG_BIN`, response bodies validated by the generated models in every test, the Report lists every TS test assertion that has no twin and why.
