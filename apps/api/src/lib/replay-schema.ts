import type { Database, Json } from "@firra/db-types";

// Migration 19 is present; the shared generated types are still owned by the lead.
// Keep this narrow extension until those types include replay_narrative.
interface NarrativeRow extends Record<string, unknown> {
  id: string;
  workspace_id: string;
  replay_run_id: string;
  client_id: string;
  version: number;
  narrative: Json;
  created_at: string;
  updated_at: string;
}
export type ReplayDatabase = Omit<Database, "public"> & {
  public: Omit<Database["public"], "Tables"> & {
    Tables: Database["public"]["Tables"] & {
      replay_narrative: {
        Row: NarrativeRow;
        Insert: Pick<
          NarrativeRow,
          "workspace_id" | "replay_run_id" | "client_id" | "version" | "narrative"
        >;
        Update: Partial<NarrativeRow>;
        Relationships: [
          {
            foreignKeyName: "replay_narrative_workspace_id_replay_run_id_fkey";
            columns: ["workspace_id", "replay_run_id"];
            isOneToOne: false;
            referencedRelation: "replay_run";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
    };
  };
};
