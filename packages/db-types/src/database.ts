export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[];

export type Database = {
  public: {
    Tables: {
      account_assessment: {
        Row: {
          as_of: string;
          assessment_version: string;
          caveats: NonNullable<Json>;
          client_id: string;
          created_at: string;
          data: NonNullable<Json>;
          drivers: NonNullable<Json>;
          expansion_state: Database["public"]["Enums"]["expansion_state"];
          id: string;
          play: string | null;
          previous_risk_state: Database["public"]["Enums"]["risk_state"] | null;
          replay_run_id: string | null;
          risk_state: Database["public"]["Enums"]["risk_state"];
          rule_version: string;
          run_kind: Database["public"]["Enums"]["assessment_run_kind"];
          state_since: string;
          strength: Database["public"]["Enums"]["evidence_strength"];
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          as_of: string;
          assessment_version: string;
          caveats?: NonNullable<Json>;
          client_id: string;
          created_at?: string;
          data?: NonNullable<Json>;
          drivers?: NonNullable<Json>;
          expansion_state: Database["public"]["Enums"]["expansion_state"];
          id?: string;
          play?: string | null;
          previous_risk_state?: Database["public"]["Enums"]["risk_state"] | null;
          replay_run_id?: string | null;
          risk_state: Database["public"]["Enums"]["risk_state"];
          rule_version: string;
          run_kind: Database["public"]["Enums"]["assessment_run_kind"];
          state_since: string;
          strength: Database["public"]["Enums"]["evidence_strength"];
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          as_of?: string;
          assessment_version?: string;
          caveats?: NonNullable<Json>;
          client_id?: string;
          created_at?: string;
          data?: NonNullable<Json>;
          drivers?: NonNullable<Json>;
          expansion_state?: Database["public"]["Enums"]["expansion_state"];
          id?: string;
          play?: string | null;
          previous_risk_state?: Database["public"]["Enums"]["risk_state"] | null;
          replay_run_id?: string | null;
          risk_state?: Database["public"]["Enums"]["risk_state"];
          rule_version?: string;
          run_kind?: Database["public"]["Enums"]["assessment_run_kind"];
          state_since?: string;
          strength?: Database["public"]["Enums"]["evidence_strength"];
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "account_assessment_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "account_assessment_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "account_assessment_workspace_id_replay_run_id_fkey";
            columns: ["workspace_id", "replay_run_id"];
            isOneToOne: false;
            referencedRelation: "replay_run";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      assessment_evidence: {
        Row: {
          assessment_id: string;
          client_id: string;
          created_at: string;
          evidence_id: string;
          id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          assessment_id: string;
          client_id: string;
          created_at?: string;
          evidence_id: string;
          id?: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          assessment_id?: string;
          client_id?: string;
          created_at?: string;
          evidence_id?: string;
          id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "assessment_evidence_workspace_id_assessment_id_client_id_fkey";
            columns: ["workspace_id", "assessment_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "account_assessment";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "assessment_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "assessment_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      assessment_signal: {
        Row: {
          assessment_id: string;
          client_id: string;
          created_at: string;
          id: string;
          signal_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          assessment_id: string;
          client_id: string;
          created_at?: string;
          id?: string;
          signal_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          assessment_id?: string;
          client_id?: string;
          created_at?: string;
          id?: string;
          signal_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "assessment_signal_workspace_id_assessment_id_client_id_fkey";
            columns: ["workspace_id", "assessment_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "account_assessment";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "assessment_signal_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "assessment_signal_workspace_id_signal_id_client_id_fkey";
            columns: ["workspace_id", "signal_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "signal";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      audit_log: {
        Row: {
          action: string;
          actor_id: string | null;
          actor_type: Database["public"]["Enums"]["actor_type"];
          created_at: string;
          id: string;
          ip: unknown;
          metadata: NonNullable<Json>;
          target_id: string | null;
          target_type: string | null;
          updated_at: string;
          user_agent: string | null;
          workspace_id: string;
        };
        Insert: {
          action: string;
          actor_id?: string | null;
          actor_type: Database["public"]["Enums"]["actor_type"];
          created_at?: string;
          id?: string;
          ip?: unknown;
          metadata?: NonNullable<Json>;
          target_id?: string | null;
          target_type?: string | null;
          updated_at?: string;
          user_agent?: string | null;
          workspace_id: string;
        };
        Update: {
          action?: string;
          actor_id?: string | null;
          actor_type?: Database["public"]["Enums"]["actor_type"];
          created_at?: string;
          id?: string;
          ip?: unknown;
          metadata?: NonNullable<Json>;
          target_id?: string | null;
          target_type?: string | null;
          updated_at?: string;
          user_agent?: string | null;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "audit_log_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      calendar_event: {
        Row: {
          attendee_count: number;
          client_id: string | null;
          created_at: string;
          duration_min: number;
          id: string;
          source_record_id: string;
          starts_at: string;
          status: Database["public"]["Enums"]["event_status"];
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          attendee_count: number;
          client_id?: string | null;
          created_at?: string;
          duration_min: number;
          id?: string;
          source_record_id: string;
          starts_at: string;
          status: Database["public"]["Enums"]["event_status"];
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          attendee_count?: number;
          client_id?: string | null;
          created_at?: string;
          duration_min?: number;
          id?: string;
          source_record_id?: string;
          starts_at?: string;
          status?: Database["public"]["Enums"]["event_status"];
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "calendar_event_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "calendar_event_workspace_id_source_record_id_fkey";
            columns: ["workspace_id", "source_record_id"];
            isOneToOne: false;
            referencedRelation: "source_record";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      client: {
        Row: {
          churned_at: string | null;
          contract_start: string | null;
          created_at: string;
          currency: string | null;
          id: string;
          last_price_increase_at: string | null;
          mrr: number | null;
          name: string;
          notice_period_days: number | null;
          policy: Database["public"]["Enums"]["data_policy"];
          renewal_date: string | null;
          service_tags: string[];
          status: Database["public"]["Enums"]["client_status"];
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          churned_at?: string | null;
          contract_start?: string | null;
          created_at?: string;
          currency?: string | null;
          id?: string;
          last_price_increase_at?: string | null;
          mrr?: number | null;
          name: string;
          notice_period_days?: number | null;
          policy?: Database["public"]["Enums"]["data_policy"];
          renewal_date?: string | null;
          service_tags?: string[];
          status?: Database["public"]["Enums"]["client_status"];
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          churned_at?: string | null;
          contract_start?: string | null;
          created_at?: string;
          currency?: string | null;
          id?: string;
          last_price_increase_at?: string | null;
          mrr?: number | null;
          name?: string;
          notice_period_days?: number | null;
          policy?: Database["public"]["Enums"]["data_policy"];
          renewal_date?: string | null;
          service_tags?: string[];
          status?: Database["public"]["Enums"]["client_status"];
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "client_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      client_card: {
        Row: {
          client_id: string;
          content: NonNullable<Json>;
          created_at: string;
          id: string;
          model: string | null;
          prompt_version: string | null;
          updated_at: string;
          version: number;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          content: NonNullable<Json>;
          created_at?: string;
          id?: string;
          model?: string | null;
          prompt_version?: string | null;
          updated_at?: string;
          version: number;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          content?: NonNullable<Json>;
          created_at?: string;
          id?: string;
          model?: string | null;
          prompt_version?: string | null;
          updated_at?: string;
          version?: number;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "client_card_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "client_card_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      client_domain: {
        Row: {
          client_id: string;
          created_at: string;
          domain: string;
          id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          domain: string;
          id?: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          domain?: string;
          id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "client_domain_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      client_goal: {
        Row: {
          client_id: string;
          created_at: string;
          dedupe_key: string;
          id: string;
          metric: string | null;
          stated_at: string;
          text: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          dedupe_key: string;
          id?: string;
          metric?: string | null;
          stated_at: string;
          text: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          dedupe_key?: string;
          id?: string;
          metric?: string | null;
          stated_at?: string;
          text?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "client_goal_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "client_goal_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      client_goal_evidence: {
        Row: {
          client_goal_id: string;
          client_id: string;
          created_at: string;
          evidence_id: string;
          id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_goal_id: string;
          client_id: string;
          created_at?: string;
          evidence_id: string;
          id?: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_goal_id?: string;
          client_id?: string;
          created_at?: string;
          evidence_id?: string;
          id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "client_goal_evidence_workspace_id_client_goal_id_client_id_fkey";
            columns: ["workspace_id", "client_goal_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client_goal";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "client_goal_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "client_goal_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      commitment: {
        Row: {
          client_id: string;
          closed_at: string | null;
          created_at: string;
          dedupe_key: string;
          due_date: string | null;
          id: string;
          owner_side: Database["public"]["Enums"]["person_side"];
          stated_at: string;
          status: Database["public"]["Enums"]["commitment_status"];
          text: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          closed_at?: string | null;
          created_at?: string;
          dedupe_key: string;
          due_date?: string | null;
          id?: string;
          owner_side: Database["public"]["Enums"]["person_side"];
          stated_at: string;
          status?: Database["public"]["Enums"]["commitment_status"];
          text: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          closed_at?: string | null;
          created_at?: string;
          dedupe_key?: string;
          due_date?: string | null;
          id?: string;
          owner_side?: Database["public"]["Enums"]["person_side"];
          stated_at?: string;
          status?: Database["public"]["Enums"]["commitment_status"];
          text?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "commitment_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "commitment_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      commitment_evidence: {
        Row: {
          client_id: string;
          commitment_id: string;
          created_at: string;
          evidence_id: string;
          id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          commitment_id: string;
          created_at?: string;
          evidence_id: string;
          id?: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          commitment_id?: string;
          created_at?: string;
          evidence_id?: string;
          id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "commitment_evidence_workspace_id_commitment_id_client_id_fkey";
            columns: ["workspace_id", "commitment_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "commitment";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "commitment_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "commitment_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      connection: {
        Row: {
          account_label: string;
          backfill_months: number;
          created_at: string;
          cursor: NonNullable<Json>;
          external_account_id: string | null;
          id: string;
          last_error_class: string | null;
          last_synced_at: string | null;
          mode: Database["public"]["Enums"]["connection_mode"];
          owner_contributor_id: string | null;
          owner_user_id: string | null;
          provider: Database["public"]["Enums"]["connection_provider"];
          scopes: string[];
          status: Database["public"]["Enums"]["connection_status"];
          token_ciphertext: string | null;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          account_label: string;
          backfill_months?: number;
          created_at?: string;
          cursor?: NonNullable<Json>;
          external_account_id?: string | null;
          id?: string;
          last_error_class?: string | null;
          last_synced_at?: string | null;
          mode: Database["public"]["Enums"]["connection_mode"];
          owner_contributor_id?: string | null;
          owner_user_id?: string | null;
          provider: Database["public"]["Enums"]["connection_provider"];
          scopes?: string[];
          status?: Database["public"]["Enums"]["connection_status"];
          token_ciphertext?: string | null;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          account_label?: string;
          backfill_months?: number;
          created_at?: string;
          cursor?: NonNullable<Json>;
          external_account_id?: string | null;
          id?: string;
          last_error_class?: string | null;
          last_synced_at?: string | null;
          mode?: Database["public"]["Enums"]["connection_mode"];
          owner_contributor_id?: string | null;
          owner_user_id?: string | null;
          provider?: Database["public"]["Enums"]["connection_provider"];
          scopes?: string[];
          status?: Database["public"]["Enums"]["connection_status"];
          token_ciphertext?: string | null;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "connection_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "connection_workspace_id_owner_contributor_id_fkey";
            columns: ["workspace_id", "owner_contributor_id"];
            isOneToOne: false;
            referencedRelation: "contributor";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      connection_scope: {
        Row: {
          client_id: string | null;
          connection_id: string;
          created_at: string;
          external_id: string;
          id: string;
          include: boolean;
          kind: Database["public"]["Enums"]["scope_kind"];
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id?: string | null;
          connection_id: string;
          created_at?: string;
          external_id: string;
          id?: string;
          include?: boolean;
          kind: Database["public"]["Enums"]["scope_kind"];
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string | null;
          connection_id?: string;
          created_at?: string;
          external_id?: string;
          id?: string;
          include?: boolean;
          kind?: Database["public"]["Enums"]["scope_kind"];
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "connection_scope_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "connection_scope_workspace_id_connection_id_fkey";
            columns: ["workspace_id", "connection_id"];
            isOneToOne: false;
            referencedRelation: "connection";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      contributor: {
        Row: {
          created_at: string;
          email: string;
          id: string;
          invited_by: string | null;
          name: string | null;
          status: Database["public"]["Enums"]["contributor_status"];
          token_hash: string | null;
          updated_at: string;
          user_id: string | null;
          workspace_id: string;
        };
        Insert: {
          created_at?: string;
          email: string;
          id?: string;
          invited_by?: string | null;
          name?: string | null;
          status?: Database["public"]["Enums"]["contributor_status"];
          token_hash?: string | null;
          updated_at?: string;
          user_id?: string | null;
          workspace_id: string;
        };
        Update: {
          created_at?: string;
          email?: string;
          id?: string;
          invited_by?: string | null;
          name?: string | null;
          status?: Database["public"]["Enums"]["contributor_status"];
          token_hash?: string | null;
          updated_at?: string;
          user_id?: string | null;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "contributor_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      decision_card: {
        Row: {
          assessment_id: string | null;
          body: string;
          caveats: NonNullable<Json>;
          client_id: string;
          created_at: string;
          dedupe_key: string;
          demoted_reason: string | null;
          facts: NonNullable<Json>;
          id: string;
          importance: Database["public"]["Enums"]["card_importance"] | null;
          inputs: NonNullable<Json>;
          interpretation: string;
          kind: Database["public"]["Enums"]["decision_kind"];
          locale: string;
          model: string;
          outcome_note: string | null;
          play: string;
          priority: number;
          prompt_version: string;
          reason_code: string | null;
          recommendation: string;
          routine_run_id: string | null;
          score: number;
          status: Database["public"]["Enums"]["decision_status"];
          strength: Database["public"]["Enums"]["evidence_strength"];
          surfaced_on: string | null;
          title: string;
          updated_at: string;
          week: string;
          workspace_id: string;
        };
        Insert: {
          assessment_id?: string | null;
          body: string;
          caveats?: NonNullable<Json>;
          client_id: string;
          created_at?: string;
          dedupe_key: string;
          demoted_reason?: string | null;
          facts: NonNullable<Json>;
          id?: string;
          importance?: Database["public"]["Enums"]["card_importance"] | null;
          inputs: NonNullable<Json>;
          interpretation: string;
          kind: Database["public"]["Enums"]["decision_kind"];
          locale: string;
          model: string;
          outcome_note?: string | null;
          play: string;
          priority: number;
          prompt_version: string;
          reason_code?: string | null;
          recommendation: string;
          routine_run_id?: string | null;
          score: number;
          status?: Database["public"]["Enums"]["decision_status"];
          strength: Database["public"]["Enums"]["evidence_strength"];
          surfaced_on?: string | null;
          title: string;
          updated_at?: string;
          week: string;
          workspace_id: string;
        };
        Update: {
          assessment_id?: string | null;
          body?: string;
          caveats?: NonNullable<Json>;
          client_id?: string;
          created_at?: string;
          dedupe_key?: string;
          demoted_reason?: string | null;
          facts?: NonNullable<Json>;
          id?: string;
          importance?: Database["public"]["Enums"]["card_importance"] | null;
          inputs?: NonNullable<Json>;
          interpretation?: string;
          kind?: Database["public"]["Enums"]["decision_kind"];
          locale?: string;
          model?: string;
          outcome_note?: string | null;
          play?: string;
          priority?: number;
          prompt_version?: string;
          reason_code?: string | null;
          recommendation?: string;
          routine_run_id?: string | null;
          score?: number;
          status?: Database["public"]["Enums"]["decision_status"];
          strength?: Database["public"]["Enums"]["evidence_strength"];
          surfaced_on?: string | null;
          title?: string;
          updated_at?: string;
          week?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "decision_card_assessment_fkey";
            columns: ["workspace_id", "assessment_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "account_assessment";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "decision_card_routine_run_fkey";
            columns: ["workspace_id", "routine_run_id"];
            isOneToOne: false;
            referencedRelation: "routine_run";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "decision_card_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "decision_card_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      decision_evidence: {
        Row: {
          client_id: string;
          created_at: string;
          decision_card_id: string;
          evidence_id: string;
          id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          decision_card_id: string;
          evidence_id: string;
          id?: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          decision_card_id?: string;
          evidence_id?: string;
          id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "decision_evidence_workspace_id_decision_card_id_client_id_fkey";
            columns: ["workspace_id", "decision_card_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "decision_card";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "decision_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "decision_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      decision_feedback: {
        Row: {
          action: Database["public"]["Enums"]["feedback_action"] | null;
          client_id: string;
          created_at: string;
          decision_card_id: string;
          id: string;
          note: string | null;
          updated_at: string;
          user_id: string;
          verdict: Database["public"]["Enums"]["feedback_verdict"];
          workspace_id: string;
        };
        Insert: {
          action?: Database["public"]["Enums"]["feedback_action"] | null;
          client_id: string;
          created_at?: string;
          decision_card_id: string;
          id?: string;
          note?: string | null;
          updated_at?: string;
          user_id: string;
          verdict: Database["public"]["Enums"]["feedback_verdict"];
          workspace_id: string;
        };
        Update: {
          action?: Database["public"]["Enums"]["feedback_action"] | null;
          client_id?: string;
          created_at?: string;
          decision_card_id?: string;
          id?: string;
          note?: string | null;
          updated_at?: string;
          user_id?: string;
          verdict?: Database["public"]["Enums"]["feedback_verdict"];
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "decision_feedback_workspace_id_decision_card_id_client_id_fkey";
            columns: ["workspace_id", "decision_card_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "decision_card";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "decision_feedback_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      evidence: {
        Row: {
          client_id: string;
          created_at: string;
          deep_link: string | null;
          id: string;
          locator: NonNullable<Json>;
          quote: string;
          source_record_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          deep_link?: string | null;
          id?: string;
          locator: NonNullable<Json>;
          quote: string;
          source_record_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          deep_link?: string | null;
          id?: string;
          locator?: NonNullable<Json>;
          quote?: string;
          source_record_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "evidence_workspace_id_source_record_id_client_id_fkey";
            columns: ["workspace_id", "source_record_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "source_record";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      extraction: {
        Row: {
          client_id: string;
          created_at: string;
          id: string;
          item_index: number;
          kind: Database["public"]["Enums"]["extraction_kind"];
          model: string;
          payload: NonNullable<Json>;
          processing_version: number;
          prompt_version: string;
          source_record_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          id?: string;
          item_index: number;
          kind: Database["public"]["Enums"]["extraction_kind"];
          model: string;
          payload: NonNullable<Json>;
          processing_version: number;
          prompt_version: string;
          source_record_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          id?: string;
          item_index?: number;
          kind?: Database["public"]["Enums"]["extraction_kind"];
          model?: string;
          payload?: NonNullable<Json>;
          processing_version?: number;
          prompt_version?: string;
          source_record_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "extraction_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "extraction_workspace_id_source_record_id_client_id_fkey";
            columns: ["workspace_id", "source_record_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "source_record";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      extraction_evidence: {
        Row: {
          client_id: string;
          created_at: string;
          evidence_id: string;
          extraction_id: string;
          id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          evidence_id: string;
          extraction_id: string;
          id?: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          evidence_id?: string;
          extraction_id?: string;
          id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "extraction_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "extraction_evidence_workspace_id_extraction_id_client_id_fkey";
            columns: ["workspace_id", "extraction_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "extraction";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "extraction_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      job: {
        Row: {
          attempts: number;
          connection_id: string | null;
          created_at: string;
          error_class: string | null;
          finished_at: string | null;
          id: string;
          idempotency_key: string;
          max_attempts: number;
          payload: NonNullable<Json>;
          progress: NonNullable<Json>;
          started_at: string | null;
          status: Database["public"]["Enums"]["job_status"];
          type: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          attempts?: number;
          connection_id?: string | null;
          created_at?: string;
          error_class?: string | null;
          finished_at?: string | null;
          id?: string;
          idempotency_key: string;
          max_attempts?: number;
          payload?: NonNullable<Json>;
          progress?: NonNullable<Json>;
          started_at?: string | null;
          status?: Database["public"]["Enums"]["job_status"];
          type: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          attempts?: number;
          connection_id?: string | null;
          created_at?: string;
          error_class?: string | null;
          finished_at?: string | null;
          id?: string;
          idempotency_key?: string;
          max_attempts?: number;
          payload?: NonNullable<Json>;
          progress?: NonNullable<Json>;
          started_at?: string | null;
          status?: Database["public"]["Enums"]["job_status"];
          type?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "job_connection_fk";
            columns: ["workspace_id", "connection_id"];
            isOneToOne: false;
            referencedRelation: "connection";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "job_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      latent_need: {
        Row: {
          client_id: string;
          cluster_key: string;
          created_at: string;
          first_seen_at: string;
          id: string;
          last_seen_at: string;
          mention_count: number;
          rule_version: string;
          status: Database["public"]["Enums"]["latent_need_status"];
          thread_count: number;
          topic: string | null;
          updated_at: string;
          workspace_id: string;
          workspace_service_id: string | null;
        };
        Insert: {
          client_id: string;
          cluster_key: string;
          created_at?: string;
          first_seen_at: string;
          id?: string;
          last_seen_at: string;
          mention_count: number;
          rule_version: string;
          status?: Database["public"]["Enums"]["latent_need_status"];
          thread_count: number;
          topic?: string | null;
          updated_at?: string;
          workspace_id: string;
          workspace_service_id?: string | null;
        };
        Update: {
          client_id?: string;
          cluster_key?: string;
          created_at?: string;
          first_seen_at?: string;
          id?: string;
          last_seen_at?: string;
          mention_count?: number;
          rule_version?: string;
          status?: Database["public"]["Enums"]["latent_need_status"];
          thread_count?: number;
          topic?: string | null;
          updated_at?: string;
          workspace_id?: string;
          workspace_service_id?: string | null;
        };
        Relationships: [
          {
            foreignKeyName: "latent_need_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "latent_need_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "latent_need_workspace_id_workspace_service_id_fkey";
            columns: ["workspace_id", "workspace_service_id"];
            isOneToOne: false;
            referencedRelation: "workspace_service";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      latent_need_evidence: {
        Row: {
          client_id: string;
          created_at: string;
          evidence_id: string;
          id: string;
          latent_need_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          evidence_id: string;
          id?: string;
          latent_need_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          evidence_id?: string;
          id?: string;
          latent_need_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "latent_need_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "latent_need_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "latent_need_evidence_workspace_id_latent_need_id_client_id_fkey";
            columns: ["workspace_id", "latent_need_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "latent_need";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      latent_need_request: {
        Row: {
          client_id: string;
          created_at: string;
          id: string;
          latent_need_id: string;
          request_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          id?: string;
          latent_need_id: string;
          request_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          id?: string;
          latent_need_id?: string;
          request_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "latent_need_request_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "latent_need_request_workspace_id_latent_need_id_client_id_fkey";
            columns: ["workspace_id", "latent_need_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "latent_need";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "latent_need_request_workspace_id_request_id_client_id_fkey";
            columns: ["workspace_id", "request_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "request";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      llm_budget_event: {
        Row: {
          created_at: string;
          id: string;
          month: string;
          threshold: number;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          created_at?: string;
          id?: string;
          month: string;
          threshold: number;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          created_at?: string;
          id?: string;
          month?: string;
          threshold?: number;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "llm_budget_event_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      llm_usage: {
        Row: {
          cost_usd: number;
          created_at: string;
          id: string;
          input_tokens: number;
          job_id: string | null;
          model: string;
          output_tokens: number;
          prompt_version: string | null;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          cost_usd: number;
          created_at?: string;
          id?: string;
          input_tokens: number;
          job_id?: string | null;
          model: string;
          output_tokens: number;
          prompt_version?: string | null;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          cost_usd?: number;
          created_at?: string;
          id?: string;
          input_tokens?: number;
          job_id?: string | null;
          model?: string;
          output_tokens?: number;
          prompt_version?: string | null;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "llm_usage_job_id_fkey";
            columns: ["job_id"];
            isOneToOne: false;
            referencedRelation: "job";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "llm_usage_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      mcp_token: {
        Row: {
          created_at: string;
          expires_at: string;
          id: string;
          last_used_at: string | null;
          name: string;
          revoked_at: string | null;
          scopes: string[];
          token_hash: string;
          token_prefix: string;
          updated_at: string;
          user_id: string;
          workspace_id: string;
        };
        Insert: {
          created_at?: string;
          expires_at: string;
          id?: string;
          last_used_at?: string | null;
          name: string;
          revoked_at?: string | null;
          scopes?: string[];
          token_hash: string;
          token_prefix: string;
          updated_at?: string;
          user_id: string;
          workspace_id: string;
        };
        Update: {
          created_at?: string;
          expires_at?: string;
          id?: string;
          last_used_at?: string | null;
          name?: string;
          revoked_at?: string | null;
          scopes?: string[];
          token_hash?: string;
          token_prefix?: string;
          updated_at?: string;
          user_id?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "mcp_token_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "mcp_token_workspace_id_user_id_fkey";
            columns: ["workspace_id", "user_id"];
            isOneToOne: false;
            referencedRelation: "membership";
            referencedColumns: ["workspace_id", "user_id"];
          },
        ];
      };
      meeting_brief: {
        Row: {
          basis: Database["public"]["Enums"]["signal_basis"];
          calendar_event_id: string;
          client_id: string;
          content: NonNullable<Json>;
          created_at: string;
          id: string;
          locale: string;
          model: string | null;
          prompt_version: string | null;
          starts_at: string;
          updated_at: string;
          version: string;
          workspace_id: string;
        };
        Insert: {
          basis: Database["public"]["Enums"]["signal_basis"];
          calendar_event_id: string;
          client_id: string;
          content: NonNullable<Json>;
          created_at?: string;
          id?: string;
          locale?: string;
          model?: string | null;
          prompt_version?: string | null;
          starts_at: string;
          updated_at?: string;
          version: string;
          workspace_id: string;
        };
        Update: {
          basis?: Database["public"]["Enums"]["signal_basis"];
          calendar_event_id?: string;
          client_id?: string;
          content?: NonNullable<Json>;
          created_at?: string;
          id?: string;
          locale?: string;
          model?: string | null;
          prompt_version?: string | null;
          starts_at?: string;
          updated_at?: string;
          version?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "meeting_brief_workspace_id_calendar_event_id_client_id_fkey";
            columns: ["workspace_id", "calendar_event_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "calendar_event";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "meeting_brief_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "meeting_brief_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      meeting_brief_evidence: {
        Row: {
          client_id: string;
          created_at: string;
          evidence_id: string;
          id: string;
          meeting_brief_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          evidence_id: string;
          id?: string;
          meeting_brief_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          evidence_id?: string;
          id?: string;
          meeting_brief_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "meeting_brief_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "meeting_brief_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "meeting_brief_evidence_workspace_id_meeting_brief_id_clien_fkey";
            columns: ["workspace_id", "meeting_brief_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "meeting_brief";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      membership: {
        Row: {
          created_at: string;
          id: string;
          role: Database["public"]["Enums"]["member_role"];
          updated_at: string;
          user_id: string;
          workspace_id: string;
        };
        Insert: {
          created_at?: string;
          id?: string;
          role: Database["public"]["Enums"]["member_role"];
          updated_at?: string;
          user_id: string;
          workspace_id: string;
        };
        Update: {
          created_at?: string;
          id?: string;
          role?: Database["public"]["Enums"]["member_role"];
          updated_at?: string;
          user_id?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "membership_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      oauth_state: {
        Row: {
          code_verifier: string;
          created_at: string;
          expires_at: string;
          id: string;
          owner_contributor_id: string | null;
          owner_user_id: string | null;
          provider: string;
          return_to: string;
          state_hash: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          code_verifier: string;
          created_at?: string;
          expires_at?: string;
          id?: string;
          owner_contributor_id?: string | null;
          owner_user_id?: string | null;
          provider: string;
          return_to: string;
          state_hash: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          code_verifier?: string;
          created_at?: string;
          expires_at?: string;
          id?: string;
          owner_contributor_id?: string | null;
          owner_user_id?: string | null;
          provider?: string;
          return_to?: string;
          state_hash?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "oauth_state_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "oauth_state_workspace_id_owner_contributor_id_fkey";
            columns: ["workspace_id", "owner_contributor_id"];
            isOneToOne: false;
            referencedRelation: "contributor";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      outcome: {
        Row: {
          amount_change: number | null;
          client_id: string;
          created_at: string;
          currency: string | null;
          event: Database["public"]["Enums"]["outcome_event"];
          first_known_at: string | null;
          id: string;
          note: string | null;
          notice_at: string | null;
          occurred_at: string;
          recorded_by: string | null;
          source: Database["public"]["Enums"]["outcome_source"];
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          amount_change?: number | null;
          client_id: string;
          created_at?: string;
          currency?: string | null;
          event: Database["public"]["Enums"]["outcome_event"];
          first_known_at?: string | null;
          id?: string;
          note?: string | null;
          notice_at?: string | null;
          occurred_at: string;
          recorded_by?: string | null;
          source?: Database["public"]["Enums"]["outcome_source"];
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          amount_change?: number | null;
          client_id?: string;
          created_at?: string;
          currency?: string | null;
          event?: Database["public"]["Enums"]["outcome_event"];
          first_known_at?: string | null;
          id?: string;
          note?: string | null;
          notice_at?: string | null;
          occurred_at?: string;
          recorded_by?: string | null;
          source?: Database["public"]["Enums"]["outcome_source"];
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "outcome_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "outcome_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      person: {
        Row: {
          client_id: string | null;
          created_at: string;
          departed_at: string | null;
          display_name: string | null;
          first_seen_at: string | null;
          id: string;
          is_champion: boolean;
          is_decision_maker: boolean;
          last_seen_at: string | null;
          merged_into_person_id: string | null;
          role_title: string | null;
          side: Database["public"]["Enums"]["person_side"];
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id?: string | null;
          created_at?: string;
          departed_at?: string | null;
          display_name?: string | null;
          first_seen_at?: string | null;
          id?: string;
          is_champion?: boolean;
          is_decision_maker?: boolean;
          last_seen_at?: string | null;
          merged_into_person_id?: string | null;
          role_title?: string | null;
          side: Database["public"]["Enums"]["person_side"];
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string | null;
          created_at?: string;
          departed_at?: string | null;
          display_name?: string | null;
          first_seen_at?: string | null;
          id?: string;
          is_champion?: boolean;
          is_decision_maker?: boolean;
          last_seen_at?: string | null;
          merged_into_person_id?: string | null;
          role_title?: string | null;
          side?: Database["public"]["Enums"]["person_side"];
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "person_merged_into_fkey";
            columns: ["workspace_id", "merged_into_person_id"];
            isOneToOne: false;
            referencedRelation: "person";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "person_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "person_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      person_identity: {
        Row: {
          created_at: string;
          id: string;
          kind: Database["public"]["Enums"]["identity_kind"];
          person_id: string;
          updated_at: string;
          value: string;
          workspace_id: string;
        };
        Insert: {
          created_at?: string;
          id?: string;
          kind: Database["public"]["Enums"]["identity_kind"];
          person_id: string;
          updated_at?: string;
          value: string;
          workspace_id: string;
        };
        Update: {
          created_at?: string;
          id?: string;
          kind?: Database["public"]["Enums"]["identity_kind"];
          person_id?: string;
          updated_at?: string;
          value?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "person_identity_workspace_id_person_id_fkey";
            columns: ["workspace_id", "person_id"];
            isOneToOne: false;
            referencedRelation: "person";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      person_observation: {
        Row: {
          client_id: string;
          created_at: string;
          id: string;
          is_decision_maker: boolean;
          observed_at: string;
          person_id: string;
          role_title: string | null;
          source: Database["public"]["Enums"]["observation_source"];
          source_record_id: string | null;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          id?: string;
          is_decision_maker?: boolean;
          observed_at: string;
          person_id: string;
          role_title?: string | null;
          source: Database["public"]["Enums"]["observation_source"];
          source_record_id?: string | null;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          id?: string;
          is_decision_maker?: boolean;
          observed_at?: string;
          person_id?: string;
          role_title?: string | null;
          source?: Database["public"]["Enums"]["observation_source"];
          source_record_id?: string | null;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "person_observation_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "person_observation_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "person_observation_workspace_id_person_id_client_id_fkey";
            columns: ["workspace_id", "person_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "person";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "person_observation_workspace_id_source_record_id_client_id_fkey";
            columns: ["workspace_id", "source_record_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "source_record";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      replay_narrative: {
        Row: {
          client_id: string;
          created_at: string;
          id: string;
          narrative: NonNullable<Json>;
          replay_run_id: string;
          updated_at: string;
          version: number;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          id?: string;
          narrative: NonNullable<Json>;
          replay_run_id: string;
          updated_at?: string;
          version: number;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          id?: string;
          narrative?: NonNullable<Json>;
          replay_run_id?: string;
          updated_at?: string;
          version?: number;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "replay_narrative_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "replay_narrative_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "replay_narrative_workspace_id_replay_run_id_fkey";
            columns: ["workspace_id", "replay_run_id"];
            isOneToOne: false;
            referencedRelation: "replay_run";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      replay_run: {
        Row: {
          assessment_version: string | null;
          created_at: string;
          error_class: string | null;
          finished_at: string | null;
          id: string;
          job_id: string | null;
          requested_by: string | null;
          rule_version: string | null;
          started_at: string | null;
          status: Database["public"]["Enums"]["replay_status"];
          step_days: number;
          summary: Json | null;
          updated_at: string;
          window_end: string;
          window_start: string;
          workspace_id: string;
        };
        Insert: {
          assessment_version?: string | null;
          created_at?: string;
          error_class?: string | null;
          finished_at?: string | null;
          id?: string;
          job_id?: string | null;
          requested_by?: string | null;
          rule_version?: string | null;
          started_at?: string | null;
          status?: Database["public"]["Enums"]["replay_status"];
          step_days?: number;
          summary?: Json | null;
          updated_at?: string;
          window_end: string;
          window_start: string;
          workspace_id: string;
        };
        Update: {
          assessment_version?: string | null;
          created_at?: string;
          error_class?: string | null;
          finished_at?: string | null;
          id?: string;
          job_id?: string | null;
          requested_by?: string | null;
          rule_version?: string | null;
          started_at?: string | null;
          status?: Database["public"]["Enums"]["replay_status"];
          step_days?: number;
          summary?: Json | null;
          updated_at?: string;
          window_end?: string;
          window_start?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "replay_run_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      request: {
        Row: {
          client_id: string;
          created_at: string;
          dedupe_key: string;
          embedding: string | null;
          embedding_model: string | null;
          id: string;
          occurred_at: string;
          origin_kind: Database["public"]["Enums"]["extraction_kind"];
          source_record_id: string;
          text: string;
          thread_key: string | null;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          dedupe_key: string;
          embedding?: string | null;
          embedding_model?: string | null;
          id?: string;
          occurred_at: string;
          origin_kind: Database["public"]["Enums"]["extraction_kind"];
          source_record_id: string;
          text: string;
          thread_key?: string | null;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          dedupe_key?: string;
          embedding?: string | null;
          embedding_model?: string | null;
          id?: string;
          occurred_at?: string;
          origin_kind?: Database["public"]["Enums"]["extraction_kind"];
          source_record_id?: string;
          text?: string;
          thread_key?: string | null;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "request_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "request_workspace_id_source_record_id_client_id_fkey";
            columns: ["workspace_id", "source_record_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "source_record";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      request_evidence: {
        Row: {
          client_id: string;
          created_at: string;
          evidence_id: string;
          id: string;
          request_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          evidence_id: string;
          id?: string;
          request_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          evidence_id?: string;
          id?: string;
          request_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "request_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "request_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "request_evidence_workspace_id_request_id_client_id_fkey";
            columns: ["workspace_id", "request_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "request";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      routine: {
        Row: {
          created_at: string;
          definition_version: string | null;
          enabled: boolean;
          id: string;
          kind: Database["public"]["Enums"]["routine_kind"];
          params: NonNullable<Json>;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          created_at?: string;
          definition_version?: string | null;
          enabled?: boolean;
          id?: string;
          kind: Database["public"]["Enums"]["routine_kind"];
          params?: NonNullable<Json>;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          created_at?: string;
          definition_version?: string | null;
          enabled?: boolean;
          id?: string;
          kind?: Database["public"]["Enums"]["routine_kind"];
          params?: NonNullable<Json>;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "routine_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      routine_run: {
        Row: {
          counts: NonNullable<Json>;
          created_at: string;
          error_class: string | null;
          finished_at: string | null;
          id: string;
          job_id: string | null;
          routine_id: string;
          scheduled_for: string;
          started_at: string | null;
          status: Database["public"]["Enums"]["routine_run_status"];
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          counts?: NonNullable<Json>;
          created_at?: string;
          error_class?: string | null;
          finished_at?: string | null;
          id?: string;
          job_id?: string | null;
          routine_id: string;
          scheduled_for: string;
          started_at?: string | null;
          status?: Database["public"]["Enums"]["routine_run_status"];
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          counts?: NonNullable<Json>;
          created_at?: string;
          error_class?: string | null;
          finished_at?: string | null;
          id?: string;
          job_id?: string | null;
          routine_id?: string;
          scheduled_for?: string;
          started_at?: string | null;
          status?: Database["public"]["Enums"]["routine_run_status"];
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "routine_run_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "routine_run_workspace_id_routine_id_fkey";
            columns: ["workspace_id", "routine_id"];
            isOneToOne: false;
            referencedRelation: "routine";
            referencedColumns: ["workspace_id", "id"];
          },
        ];
      };
      signal: {
        Row: {
          basis: Database["public"]["Enums"]["signal_basis"];
          client_id: string;
          created_at: string;
          dedupe_key: string;
          family: string | null;
          id: string;
          inputs: Json | null;
          occurred_at: string;
          resolved_at: string | null;
          rule_version: string;
          severity: Database["public"]["Enums"]["signal_severity"];
          type: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          basis: Database["public"]["Enums"]["signal_basis"];
          client_id: string;
          created_at?: string;
          dedupe_key: string;
          family?: string | null;
          id?: string;
          inputs?: Json | null;
          occurred_at: string;
          resolved_at?: string | null;
          rule_version: string;
          severity: Database["public"]["Enums"]["signal_severity"];
          type: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          basis?: Database["public"]["Enums"]["signal_basis"];
          client_id?: string;
          created_at?: string;
          dedupe_key?: string;
          family?: string | null;
          id?: string;
          inputs?: Json | null;
          occurred_at?: string;
          resolved_at?: string | null;
          rule_version?: string;
          severity?: Database["public"]["Enums"]["signal_severity"];
          type?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "signal_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "signal_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      signal_event: {
        Row: {
          as_of: string;
          change: Database["public"]["Enums"]["signal_change"];
          client_id: string;
          created_at: string;
          id: string;
          inputs: Json | null;
          job_id: string | null;
          rule_version: string;
          severity: Database["public"]["Enums"]["signal_severity"];
          signal_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          as_of: string;
          change: Database["public"]["Enums"]["signal_change"];
          client_id: string;
          created_at?: string;
          id?: string;
          inputs?: Json | null;
          job_id?: string | null;
          rule_version: string;
          severity: Database["public"]["Enums"]["signal_severity"];
          signal_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          as_of?: string;
          change?: Database["public"]["Enums"]["signal_change"];
          client_id?: string;
          created_at?: string;
          id?: string;
          inputs?: Json | null;
          job_id?: string | null;
          rule_version?: string;
          severity?: Database["public"]["Enums"]["signal_severity"];
          signal_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "signal_event_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "signal_event_workspace_id_signal_id_client_id_fkey";
            columns: ["workspace_id", "signal_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "signal";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      signal_evidence: {
        Row: {
          client_id: string;
          created_at: string;
          evidence_id: string;
          id: string;
          signal_id: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          client_id: string;
          created_at?: string;
          evidence_id: string;
          id?: string;
          signal_id: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          client_id?: string;
          created_at?: string;
          evidence_id?: string;
          id?: string;
          signal_id?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "signal_evidence_workspace_id_evidence_id_client_id_fkey";
            columns: ["workspace_id", "evidence_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "evidence";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
          {
            foreignKeyName: "signal_evidence_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "signal_evidence_workspace_id_signal_id_client_id_fkey";
            columns: ["workspace_id", "signal_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "signal";
            referencedColumns: ["workspace_id", "id", "client_id"];
          },
        ];
      };
      source_record: {
        Row: {
          author_person_id: string | null;
          body_clean: string | null;
          body_purged_at: string | null;
          client_id: string | null;
          connection_id: string;
          content_hash: string | null;
          created_at: string;
          deep_link: string | null;
          direction: Database["public"]["Enums"]["record_direction"];
          external_id: string;
          id: string;
          kind: Database["public"]["Enums"]["record_kind"];
          metadata: NonNullable<Json>;
          occurred_at: string;
          participants: string[];
          policy_at_ingest: Database["public"]["Enums"]["data_policy"];
          processed_at: string | null;
          processing_state: Database["public"]["Enums"]["processing_state"];
          processing_version: number;
          provider: Database["public"]["Enums"]["connection_provider"];
          raw_object_uri: string | null;
          rehydrated_at: string | null;
          thread_key: string | null;
          updated_at: string;
          vault_delivered_at: string | null;
          vault_user_id: string | null;
          word_count: number;
          workspace_id: string;
        };
        Insert: {
          author_person_id?: string | null;
          body_clean?: string | null;
          body_purged_at?: string | null;
          client_id?: string | null;
          connection_id: string;
          content_hash?: string | null;
          created_at?: string;
          deep_link?: string | null;
          direction: Database["public"]["Enums"]["record_direction"];
          external_id: string;
          id?: string;
          kind: Database["public"]["Enums"]["record_kind"];
          metadata?: NonNullable<Json>;
          occurred_at: string;
          participants?: string[];
          policy_at_ingest: Database["public"]["Enums"]["data_policy"];
          processed_at?: string | null;
          processing_state?: Database["public"]["Enums"]["processing_state"];
          processing_version?: number;
          provider: Database["public"]["Enums"]["connection_provider"];
          raw_object_uri?: string | null;
          rehydrated_at?: string | null;
          thread_key?: string | null;
          updated_at?: string;
          vault_delivered_at?: string | null;
          vault_user_id?: string | null;
          word_count?: number;
          workspace_id: string;
        };
        Update: {
          author_person_id?: string | null;
          body_clean?: string | null;
          body_purged_at?: string | null;
          client_id?: string | null;
          connection_id?: string;
          content_hash?: string | null;
          created_at?: string;
          deep_link?: string | null;
          direction?: Database["public"]["Enums"]["record_direction"];
          external_id?: string;
          id?: string;
          kind?: Database["public"]["Enums"]["record_kind"];
          metadata?: NonNullable<Json>;
          occurred_at?: string;
          participants?: string[];
          policy_at_ingest?: Database["public"]["Enums"]["data_policy"];
          processed_at?: string | null;
          processing_state?: Database["public"]["Enums"]["processing_state"];
          processing_version?: number;
          provider?: Database["public"]["Enums"]["connection_provider"];
          raw_object_uri?: string | null;
          rehydrated_at?: string | null;
          thread_key?: string | null;
          updated_at?: string;
          vault_delivered_at?: string | null;
          vault_user_id?: string | null;
          word_count?: number;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "source_record_workspace_id_author_person_id_fkey";
            columns: ["workspace_id", "author_person_id"];
            isOneToOne: false;
            referencedRelation: "person";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "source_record_workspace_id_client_id_fkey";
            columns: ["workspace_id", "client_id"];
            isOneToOne: false;
            referencedRelation: "client";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "source_record_workspace_id_connection_id_fkey";
            columns: ["workspace_id", "connection_id"];
            isOneToOne: false;
            referencedRelation: "connection";
            referencedColumns: ["workspace_id", "id"];
          },
          {
            foreignKeyName: "source_record_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      workspace: {
        Row: {
          brief_day: number;
          brief_hour: number;
          created_at: string;
          default_locale: Database["public"]["Enums"]["app_locale"];
          id: string;
          internal_mentions: boolean;
          llm_budget_usd_month: number;
          name: string;
          timezone: string;
          updated_at: string;
        };
        Insert: {
          brief_day?: number;
          brief_hour?: number;
          created_at?: string;
          default_locale?: Database["public"]["Enums"]["app_locale"];
          id?: string;
          internal_mentions?: boolean;
          llm_budget_usd_month?: number;
          name: string;
          timezone?: string;
          updated_at?: string;
        };
        Update: {
          brief_day?: number;
          brief_hour?: number;
          created_at?: string;
          default_locale?: Database["public"]["Enums"]["app_locale"];
          id?: string;
          internal_mentions?: boolean;
          llm_budget_usd_month?: number;
          name?: string;
          timezone?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      workspace_invite: {
        Row: {
          accepted_at: string | null;
          accepted_by: string | null;
          created_at: string;
          email: string;
          expires_at: string;
          id: string;
          invited_by: string | null;
          revoked_at: string | null;
          role: Database["public"]["Enums"]["member_role"];
          token_hash: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          accepted_at?: string | null;
          accepted_by?: string | null;
          created_at?: string;
          email: string;
          expires_at: string;
          id?: string;
          invited_by?: string | null;
          revoked_at?: string | null;
          role?: Database["public"]["Enums"]["member_role"];
          token_hash: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          accepted_at?: string | null;
          accepted_by?: string | null;
          created_at?: string;
          email?: string;
          expires_at?: string;
          id?: string;
          invited_by?: string | null;
          revoked_at?: string | null;
          role?: Database["public"]["Enums"]["member_role"];
          token_hash?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "workspace_invite_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      workspace_key: {
        Row: {
          created_at: string;
          id: string;
          kek_name: string;
          updated_at: string;
          workspace_id: string;
          wrapped_dek: string;
        };
        Insert: {
          created_at?: string;
          id?: string;
          kek_name: string;
          updated_at?: string;
          workspace_id: string;
          wrapped_dek: string;
        };
        Update: {
          created_at?: string;
          id?: string;
          kek_name?: string;
          updated_at?: string;
          workspace_id?: string;
          wrapped_dek?: string;
        };
        Relationships: [
          {
            foreignKeyName: "workspace_key_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: true;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
      workspace_service: {
        Row: {
          created_at: string;
          description: string;
          id: string;
          keywords: string[];
          name: string;
          updated_at: string;
          workspace_id: string;
        };
        Insert: {
          created_at?: string;
          description?: string;
          id?: string;
          keywords?: string[];
          name: string;
          updated_at?: string;
          workspace_id: string;
        };
        Update: {
          created_at?: string;
          description?: string;
          id?: string;
          keywords?: string[];
          name?: string;
          updated_at?: string;
          workspace_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "workspace_service_workspace_id_fkey";
            columns: ["workspace_id"];
            isOneToOne: false;
            referencedRelation: "workspace";
            referencedColumns: ["id"];
          },
        ];
      };
    };
    Views: {
      [_ in never]: never;
    };
    Functions: {
      accept_workspace_invite: { Args: { p_token: string }; Returns: string };
      claim_contributor_invite: {
        Args: { p_token: string };
        Returns: {
          out_contributor_id: string;
          out_workspace_id: string;
        }[];
      };
      create_client: {
        Args: {
          p_domains?: string[];
          p_name: string;
          p_policy?: Database["public"]["Enums"]["data_policy"];
          p_status?: Database["public"]["Enums"]["client_status"];
          p_workspace_id: string;
        };
        Returns: string;
      };
      create_contributor_invite: {
        Args: { p_email: string; p_name?: string; p_workspace_id: string };
        Returns: {
          contributor_id: string;
          token: string;
        }[];
      };
      create_mcp_token: {
        Args: { p_days?: number; p_name: string; p_workspace_id: string };
        Returns: {
          expires_at: string;
          token: string;
          token_id: string;
          token_prefix: string;
        }[];
      };
      create_workspace: {
        Args: {
          p_default_locale?: Database["public"]["Enums"]["app_locale"];
          p_name: string;
          p_timezone?: string;
        };
        Returns: string;
      };
      create_workspace_invite: {
        Args: { p_email: string; p_workspace_id: string };
        Returns: {
          expires_at: string;
          invite_id: string;
          token: string;
        }[];
      };
      delete_client_outcome: { Args: { p_outcome_id: string }; Returns: undefined };
      delete_workspace_service: { Args: { p_service_id: string }; Returns: undefined };
      disconnect_connection: { Args: { p_connection_id: string }; Returns: undefined };
      disconnect_own_connection: { Args: { p_connection_id: string }; Returns: undefined };
      list_workspace_members: {
        Args: { p_workspace_id: string };
        Returns: {
          email: string;
          joined_at: string;
          role: Database["public"]["Enums"]["member_role"];
          user_id: string;
        }[];
      };
      my_contributions: {
        Args: Record<PropertyKey, never>;
        Returns: {
          contributor_id: string;
          status: Database["public"]["Enums"]["contributor_status"];
          workspace_id: string;
          workspace_name: string;
        }[];
      };
      record_client_outcome: {
        Args: {
          p_amount_change?: number;
          p_client_id: string;
          p_currency?: string;
          p_event: Database["public"]["Enums"]["outcome_event"];
          p_first_known_at?: string;
          p_note?: string;
          p_notice_at?: string;
          p_occurred_at: string;
        };
        Returns: string;
      };
      record_decision_feedback: {
        Args: {
          p_action?: Database["public"]["Enums"]["feedback_action"];
          p_decision_card_id: string;
          p_note?: string;
          p_verdict: Database["public"]["Enums"]["feedback_verdict"];
        };
        Returns: string;
      };
      remove_member: { Args: { p_user_id: string; p_workspace_id: string }; Returns: undefined };
      request_replay_run: {
        Args: {
          p_step_days?: number;
          p_window_end: string;
          p_window_start: string;
          p_workspace_id: string;
        };
        Returns: string;
      };
      revoke_contributor: { Args: { p_contributor_id: string }; Returns: undefined };
      revoke_mcp_token: { Args: { p_token_id: string }; Returns: undefined };
      revoke_workspace_invite: { Args: { p_invite_id: string }; Returns: undefined };
      save_workspace_service: {
        Args: {
          p_description?: string;
          p_keywords?: string[];
          p_name: string;
          p_service_id: string;
          p_workspace_id: string;
        };
        Returns: string;
      };
      set_client_domains: {
        Args: { p_client_id: string; p_domains: string[] };
        Returns: undefined;
      };
      set_client_policy: {
        Args: { p_client_id: string; p_policy: Database["public"]["Enums"]["data_policy"] };
        Returns: undefined;
      };
      set_routine_enabled: {
        Args: {
          p_enabled: boolean;
          p_kind: Database["public"]["Enums"]["routine_kind"];
          p_workspace_id: string;
        };
        Returns: string;
      };
      update_client: {
        Args: {
          p_client_id: string;
          p_name: string;
          p_status: Database["public"]["Enums"]["client_status"];
        };
        Returns: undefined;
      };
    };
    Enums: {
      actor_type: "user" | "mcp" | "system";
      app_locale: "en" | "de" | "sv" | "fr" | "it";
      assessment_run_kind: "live" | "replay";
      card_importance: "fyi" | "attention" | "important";
      client_status: "active" | "churned" | "prospect";
      commitment_status: "open" | "done" | "late" | "cancelled";
      connection_mode: "user" | "org_delegated" | "org_app" | "upload";
      connection_provider:
        "gmail" | "google_calendar" | "outlook" | "outlook_calendar" | "email_upload";
      connection_status: "active" | "error" | "revoked";
      contributor_status: "invited" | "active" | "revoked";
      data_policy: "full" | "metadata_only" | "excluded";
      decision_kind:
        | "risk"
        | "margin"
        | "pricing"
        | "growth"
        | "external"
        | "commitment"
        | "state_change"
        | "commitment_watch"
        | "expansion";
      decision_status: "new" | "done" | "dismissed";
      event_status: "scheduled" | "held" | "cancelled" | "rescheduled";
      evidence_strength: "weak" | "moderate" | "strong";
      expansion_state: "none" | "hypothesis" | "strong";
      extraction_kind:
        | "request"
        | "commitment"
        | "praise"
        | "complaint"
        | "goal"
        | "stakeholder_change"
        | "in_house_or_competitor_mention"
        | "renewal_or_budget_talk"
        | "result_reported"
        | "tone"
        | "initiative";
      feedback_action: "taken" | "planned" | "none";
      feedback_verdict: "useful" | "already_known" | "wrong" | "not_now";
      identity_kind: "email" | "slack_id" | "pm_id" | "crm_id";
      job_status: "queued" | "running" | "succeeded" | "failed";
      latent_need_status: "open" | "addressed" | "dismissed";
      member_role: "owner" | "admin";
      observation_source: "signature" | "stakeholder_change" | "manual";
      outcome_event:
        "renewed" | "price_increase" | "downgrade" | "expanded" | "churned" | "referral";
      outcome_source: "user" | "crm" | "contract";
      person_side: "client" | "firm";
      processing_state: "pending" | "done" | "error" | "skipped_purged" | "not_applicable";
      record_direction: "in" | "out" | "internal";
      record_kind:
        | "email"
        | "chat"
        | "call"
        | "transcript"
        | "task"
        | "comment"
        | "time_entry"
        | "invoice"
        | "contract"
        | "calendar_event"
        | "crm_note";
      replay_status: "queued" | "running" | "succeeded" | "failed";
      risk_state: "stable" | "watch" | "elevated" | "high";
      routine_kind: "morning_scan" | "commitment_watch" | "pre_meeting_brief";
      routine_run_status: "queued" | "running" | "succeeded" | "failed";
      scope_kind: "user" | "channel" | "project" | "calendar";
      signal_basis: "evidence" | "metadata";
      signal_change: "opened" | "severity_changed" | "reopened" | "resolved";
      signal_severity: "low" | "medium" | "high";
    };
    CompositeTypes: {
      [_ in never]: never;
    };
  };
};

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">;

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">];

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R;
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] & DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R;
      }
      ? R
      : never
    : never;

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    keyof DefaultSchema["Tables"] | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I;
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I;
      }
      ? I
      : never
    : never;

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    keyof DefaultSchema["Tables"] | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U;
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U;
      }
      ? U
      : never
    : never;

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    keyof DefaultSchema["Enums"] | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never;

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    keyof DefaultSchema["CompositeTypes"] | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never;

export const Constants = {
  public: {
    Enums: {
      actor_type: ["user", "mcp", "system"],
      app_locale: ["en", "de", "sv", "fr", "it"],
      assessment_run_kind: ["live", "replay"],
      card_importance: ["fyi", "attention", "important"],
      client_status: ["active", "churned", "prospect"],
      commitment_status: ["open", "done", "late", "cancelled"],
      connection_mode: ["user", "org_delegated", "org_app", "upload"],
      connection_provider: [
        "gmail",
        "google_calendar",
        "outlook",
        "outlook_calendar",
        "email_upload",
      ],
      connection_status: ["active", "error", "revoked"],
      contributor_status: ["invited", "active", "revoked"],
      data_policy: ["full", "metadata_only", "excluded"],
      decision_kind: [
        "risk",
        "margin",
        "pricing",
        "growth",
        "external",
        "commitment",
        "state_change",
        "commitment_watch",
        "expansion",
      ],
      decision_status: ["new", "done", "dismissed"],
      event_status: ["scheduled", "held", "cancelled", "rescheduled"],
      evidence_strength: ["weak", "moderate", "strong"],
      expansion_state: ["none", "hypothesis", "strong"],
      extraction_kind: [
        "request",
        "commitment",
        "praise",
        "complaint",
        "goal",
        "stakeholder_change",
        "in_house_or_competitor_mention",
        "renewal_or_budget_talk",
        "result_reported",
        "tone",
        "initiative",
      ],
      feedback_action: ["taken", "planned", "none"],
      feedback_verdict: ["useful", "already_known", "wrong", "not_now"],
      identity_kind: ["email", "slack_id", "pm_id", "crm_id"],
      job_status: ["queued", "running", "succeeded", "failed"],
      latent_need_status: ["open", "addressed", "dismissed"],
      member_role: ["owner", "admin"],
      observation_source: ["signature", "stakeholder_change", "manual"],
      outcome_event: ["renewed", "price_increase", "downgrade", "expanded", "churned", "referral"],
      outcome_source: ["user", "crm", "contract"],
      person_side: ["client", "firm"],
      processing_state: ["pending", "done", "error", "skipped_purged", "not_applicable"],
      record_direction: ["in", "out", "internal"],
      record_kind: [
        "email",
        "chat",
        "call",
        "transcript",
        "task",
        "comment",
        "time_entry",
        "invoice",
        "contract",
        "calendar_event",
        "crm_note",
      ],
      replay_status: ["queued", "running", "succeeded", "failed"],
      risk_state: ["stable", "watch", "elevated", "high"],
      routine_kind: ["morning_scan", "commitment_watch", "pre_meeting_brief"],
      routine_run_status: ["queued", "running", "succeeded", "failed"],
      scope_kind: ["user", "channel", "project", "calendar"],
      signal_basis: ["evidence", "metadata"],
      signal_change: ["opened", "severity_changed", "reopened", "resolved"],
      signal_severity: ["low", "medium", "high"],
    },
  },
} as const;
