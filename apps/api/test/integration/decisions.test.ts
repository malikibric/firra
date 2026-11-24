/** Synthetic local Supabase proof: caller RLS, real RPC audit and feedback/outcome effects. */
import { randomUUID } from "node:crypto";
import { decisionsResponse, mutationResponse } from "@firra/contracts";
import { expect, it } from "vitest";
import { admin, appFor, userWithToken, workspace } from "./read-helpers.js";

it("lists visible cards and closes the loop through caller RPCs with exactly their audit rows", async () => {
  const owner = await userWithToken("decisions-owner");
  const other = await userWithToken("decisions-admin");
  const outsider = await userWithToken("decisions-outsider");
  const contributor = await userWithToken("decisions-contributor");
  const ws = await workspace();
  const foreignWs = await workspace();
  const app = appFor();
  const headers = { authorization: `Bearer ${owner.token}`, "content-type": "application/json" };
  const base = `/v1/workspaces/${ws}`;
  try {
    const members = await admin.from("membership").insert([
      { workspace_id: ws, user_id: owner.id, role: "owner" },
      { workspace_id: ws, user_id: other.id, role: "admin" },
      { workspace_id: foreignWs, user_id: owner.id, role: "owner" },
    ]);
    expect(members.error).toBeNull();
    expect(
      (await admin.from("contributor").insert({ workspace_id: ws, email: contributor.email }))
        .error,
    ).toBeNull();
    const [clientId, excludedId, metadataId, foreignId] = Array.from({ length: 4 }, () =>
      randomUUID(),
    );
    if (!clientId || !excludedId || !metadataId || !foreignId)
      throw new Error("fixture ids missing");
    expect(
      (
        await admin.from("client").insert([
          { id: clientId, workspace_id: ws, name: "Synthetic full", policy: "full" },
          { id: excludedId, workspace_id: ws, name: "Synthetic excluded", policy: "excluded" },
          { id: metadataId, workspace_id: ws, name: "Synthetic metadata", policy: "metadata_only" },
          { id: foreignId, workspace_id: foreignWs, name: "Synthetic foreign", policy: "full" },
        ])
      ).error,
    ).toBeNull();
    const ids = [randomUUID(), randomUUID(), randomUUID(), randomUUID()];
    expect(
      (
        await admin.from("decision_card").insert(
          [clientId, excludedId, metadataId, foreignId].map((id, index) => ({
            id: ids[index],
            workspace_id: index === 3 ? foreignWs : ws,
            client_id: id,
            week: "2026-09-28",
            kind: "risk" as const,
            title: "Synthetic title",
            body: "Synthetic body",
            locale: "en",
            score: 1,
            priority: 1,
            inputs: {},
            model: "fixture",
            prompt_version: "fixture",
            dedupe_key: `fixture-${id}`,
            play: "fix_first",
            facts: [{ basis: "metadata" }],
            interpretation: "Synthetic interpretation",
            recommendation: "Synthetic recommendation",
            strength: "strong" as const,
          })),
        )
      ).error,
    ).toBeNull();
    const cardId = ids[0];
    if (!cardId) throw new Error("fixture card missing");
    const list = async (token = owner.token) => {
      const res = await app.request(`${base}/decisions?status=all`, {
        headers: { authorization: `Bearer ${token}` },
      });
      expect(res.status).toBe(200);
      return decisionsResponse.parse(await res.json());
    };
    expect((await list()).items.map((c) => c.id).sort()).toEqual([cardId, ids[2]].sort());
    expect((await list()).items.find((c) => c.client_id === metadataId)?.evidence_ids).toEqual([]);
    const connection = await admin
      .from("connection")
      .insert({
        workspace_id: ws,
        provider: "email_upload",
        mode: "upload",
        account_label: "Synthetic",
      })
      .select("id")
      .single();
    if (!connection.data) throw new Error("synthetic connection failed");
    const record = await admin
      .from("source_record")
      .insert({
        workspace_id: ws,
        client_id: clientId,
        connection_id: connection.data.id,
        provider: "email_upload",
        kind: "email",
        direction: "in",
        policy_at_ingest: "full",
        external_id: randomUUID(),
        occurred_at: "2026-09-28T00:00:00Z",
        body_clean: "Synthetic verified quote",
      })
      .select("id")
      .single();
    if (!record.data) throw new Error("synthetic record failed");
    const evidence = await admin
      .from("evidence")
      .insert({
        workspace_id: ws,
        client_id: clientId,
        source_record_id: record.data.id,
        quote: "Synthetic verified quote",
        locator: { start: 0, end: 24 },
      })
      .select("id")
      .single();
    if (!evidence.data) throw new Error("synthetic evidence failed");
    expect(
      (
        await admin.from("decision_evidence").insert({
          workspace_id: ws,
          client_id: clientId,
          decision_card_id: cardId,
          evidence_id: evidence.data.id,
        })
      ).error,
    ).toBeNull();
    const feedbackUrl = `${base}/decisions/${cardId}/feedback`;
    let feedbackId = "";
    for (const body of [
      { verdict: "wrong", note: "Synthetic note" },
      { verdict: "useful", action: "taken" },
    ]) {
      const res = await app.request(feedbackUrl, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
      expect(res.status).toBe(200);
      const id = mutationResponse.parse(await res.json()).id;
      if (feedbackId) expect(id).toBe(feedbackId);
      feedbackId = id;
    }
    const latest = (await list()).items.find((c) => c.id === cardId);
    expect(latest?.evidence_ids).toEqual([evidence.data.id]);
    expect(latest?.status).toBe("done");
    expect(latest?.feedback).toEqual({ verdict: "useful", action: "taken", note: null });
    expect((await list(other.token)).items.find((c) => c.id === cardId)?.feedback).toBeNull();
    for (const id of [ids[1], ids[3], randomUUID()]) {
      const res = await app.request(`${base}/decisions/${id}/feedback`, {
        method: "POST",
        headers,
        body: '{"verdict":"useful"}',
      });
      expect(res.status).toBe(404);
    }
    expect(
      (
        await app.request(`${base}/decisions`, {
          headers: { authorization: `Bearer ${outsider.token}` },
        })
      ).status,
    ).toBe(403);
    expect(
      (
        await app.request(feedbackUrl, {
          method: "POST",
          headers: {
            authorization: `Bearer ${contributor.token}`,
            "content-type": "application/json",
          },
          body: '{"verdict":"useful"}',
        })
      ).status,
    ).toBe(403);
    for (const client of [foreignId, excludedId])
      expect(
        (
          await app.request(`${base}/clients/${client}/outcomes`, {
            method: "POST",
            headers,
            body: '{"event":"churned","occurred_at":"2026-10-01"}',
          })
        ).status,
      ).toBe(404);
    const created = await app.request(`${base}/clients/${clientId}/outcomes`, {
      method: "POST",
      headers,
      body: '{"event":"churned","occurred_at":"2026-10-01","notice_at":"2026-09-01"}',
    });
    expect(created.status).toBe(200);
    const outcomeId = mutationResponse.parse(await created.json()).id;
    expect(
      (await admin.from("client").select("status, churned_at").eq("id", clientId).single()).data,
    ).toEqual({ status: "churned", churned_at: "2026-10-01" });
    const wrongWorkspace = await app.request(`/v1/workspaces/${foreignWs}/outcomes/${outcomeId}`, {
      method: "DELETE",
      headers,
    });
    expect(wrongWorkspace.status).toBe(404);
    expect(
      (await app.request(`${base}/outcomes/${outcomeId}`, { method: "DELETE", headers })).status,
    ).toBe(204);
    expect(
      (await admin.from("client").select("status, churned_at").eq("id", clientId).single()).data,
    ).toEqual({ status: "active", churned_at: null });
    const audit = await admin.from("audit_log").select("action").eq("workspace_id", ws);
    expect(audit.error).toBeNull();
    expect(audit.data?.map((a) => a.action).sort()).toEqual(
      [
        "read.decisions",
        "read.decisions",
        "read.decisions",
        "read.decisions",
        "decision_feedback.recorded",
        "decision_feedback.recorded",
        "outcome.recorded",
        "outcome.deleted",
      ].sort(),
    );
    // Routine cards (T43a): importance and kind filters run as the caller through RLS.
    const routineIds = [randomUUID(), randomUUID(), randomUUID()];
    expect(
      (
        await admin.from("decision_card").insert(
          [
            { id: clientId, importance: "important" as const, kind: "state_change" as const },
            { id: excludedId, importance: "important" as const, kind: "state_change" as const },
            { id: metadataId, importance: "fyi" as const, kind: "expansion" as const },
          ].map(({ id, importance, kind }, index) => ({
            id: routineIds[index],
            workspace_id: ws,
            client_id: id,
            week: "2026-10-05",
            kind,
            importance,
            surfaced_on: "2026-10-06",
            reason_code: "risk_rose_elevated",
            title: "Synthetic routine title",
            body: "Synthetic routine body",
            locale: "en",
            score: 1,
            priority: 1,
            inputs: {},
            model: "deterministic",
            prompt_version: "facts.v1",
            dedupe_key: `routine-${id}`,
            play: "fix_first",
            facts: [{ basis: "metadata" }],
            interpretation: "Synthetic interpretation",
            recommendation: "Synthetic recommendation",
            strength: "strong" as const,
          })),
        )
      ).error,
    ).toBeNull();
    const filtered = async (query: string) => {
      const res = await app.request(`${base}/decisions?status=all&${query}`, {
        headers: { authorization: `Bearer ${owner.token}` },
      });
      expect(res.status).toBe(200);
      return decisionsResponse.parse(await res.json());
    };
    const important = await filtered("importance=important");
    expect(important.items.map((c) => c.id)).toEqual([routineIds[0]]); // the excluded client's card is absent
    expect(important.items[0]).toMatchObject({
      importance: "important",
      surfaced_on: "2026-10-06",
    });
    expect((await filtered("kind=expansion")).items.map((c) => c.id)).toEqual([routineIds[2]]);
    expect((await filtered("importance=fyi&kind=state_change")).items).toEqual([]);
    expect((await filtered("kind=risk")).items.map((c) => c.id).sort()).toEqual(
      [cardId, ids[2]].sort(),
    );
    expect((await list()).items.find((c) => c.id === ids[0])?.importance ?? null).toBeNull();
  } finally {
    await admin.from("workspace").delete().in("id", [ws, foreignWs]);
    for (const user of [owner, other, outsider, contributor])
      await admin.auth.admin.deleteUser(user.id);
  }
}, 30000);
