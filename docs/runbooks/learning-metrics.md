# Learning metrics (local only)

From `services/worker`, set `FIRRA_DATABASE_URL` to your **local synthetic/fixture** Postgres URL,
then run `uv run python -m firra.learning report --workspace UUID --as-of 2026-10-03`.
Add `--json` for aggregate JSON; without `--as-of`, the CLI uses today's UTC date.
The command reads only, calls no model, excludes excluded clients, and never selects notes or names.

- **Usefulness:** Counts are grouped by the card's own kind and play; `rated` excludes `not_now`.
  Rates use rated feedback, become null below `min_rated`, and `acted` counts taken/planned useful rows.
- **Episodes:** A warning starts at the first of two consecutive elevated/high live assessments.
  A lower state ends the run; families come only from the start row's primary drivers.
- **Precision:** Each episode is confirmed by a churn/downgrade within `(start, start + horizon]`;
  renewal/expansion or an elapsed horizon closes it unconfirmed, otherwise it stays open.
  The rate excludes open episodes and becomes null below `min_rated`; each distinct family counts once.

Defaults in `config/learning.yaml`: version `learning.v1`, horizon 180 days, minimum 5 rated rows.
Markdown rounds rates to three decimals; JSON retains mathematical precision.
Null means no denominator or an insufficient sample, never zero quality.
Small samples are unstable; one founder's feedback is not a population or an employee comparison.
The horizon censors late consequences; matching an outcome does not establish causation.
Multiple episodes can match one outcome for precision.
As-of filters assessment/outcome dates and feedback/card timestamps; feedback is mutable, so this
cannot reconstruct a feedback verdict replaced later or historical policy changes.

**Lead time** scores each recorded churn/downgrade separately using the earliest same-client
episode in the preceding 365 days, strictly before `notice_at` when present or `occurred_at`
otherwise; using the notice date makes the warning honest about when the firm could still act.
Detected/missed and recall are global; each selected episode's distinct families receive detected,
median days and share at least 30 days only (the Markdown family rows show `null` for the global-only
missed/recall columns). Medians use the lower middle value for an even sample, and empty denominators
are `null`. The 365-day window censors earlier warnings, this metric does not use precision's
180-day horizon, and clients without a recorded negative outcome are not in its denominator.
