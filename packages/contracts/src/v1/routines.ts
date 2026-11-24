import { z } from "zod";

export const routineKind = z.enum(["morning_scan", "commitment_watch", "pre_meeting_brief"]);
export type RoutineKind = z.infer<typeof routineKind>;
export const routineEnabledRequest = z.object({ enabled: z.boolean() }).strict();
export type RoutineEnabledRequest = z.infer<typeof routineEnabledRequest>;
export const routineLastRun = z.object({
  scheduled_for: z.iso.date(),
  status: z.enum(["queued", "running", "succeeded", "failed"]),
  finished_at: z.iso.datetime({ offset: true }).nullable(),
  counts: z.record(z.string(), z.number()),
});
export const routineItem = z.object({
  kind: routineKind,
  enabled: z.boolean(),
  created: z.boolean(),
  last_run: routineLastRun.nullable(),
});
export type RoutineItem = z.infer<typeof routineItem>;
export const routinesResponse = z.object({
  generated_at: z.iso.datetime({ offset: true }),
  items: z.array(routineItem).length(3),
});
