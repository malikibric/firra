variable "project_id" {
  description = "GCP project for this environment (e.g. firra-staging-xxxx)."
  type        = string
}

variable "environment" {
  description = "staging or prod."
  type        = string
  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be staging or prod."
  }
}

variable "region" {
  description = "All resources stay in the EU (Frankfurt)."
  type        = string
  default     = "europe-west3"
}

variable "billing_account_id" {
  description = "Billing account for the budget alert (XXXXXX-XXXXXX-XXXXXX)."
  type        = string
}

variable "budget_amount" {
  description = "Monthly budget; alerts at 50/80/100%."
  type        = number
}

variable "budget_currency" {
  description = "Must match the billing account currency."
  type        = string
  default     = "USD"
}

variable "alert_email" {
  description = "Receives budget and uptime alerts."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to deploy."
  type        = string
}

variable "github_repository_id" {
  description = "Numeric GitHub repository id (pins trust even if the name is reused)."
  type        = string
}

variable "github_environment" {
  description = "GitHub Actions environment whose jobs may deploy here (staging or production)."
  type        = string
}

variable "supabase_url" {
  description = "https://<ref>.supabase.co of this environment's Supabase project."
  type        = string
  validation {
    condition     = can(regex("^https://[a-z0-9]+\\.supabase\\.co$", var.supabase_url))
    error_message = "supabase_url must be the bare project URL, e.g. https://abcd1234.supabase.co (no path, no trailing slash)."
  }
}

variable "supabase_publishable_key" {
  description = "Supabase publishable (anon) key. Not a secret."
  type        = string
}

variable "create_services" {
  description = "Set false for the first apply, add secret versions, then apply with true."
  type        = bool
  default     = true
}

variable "enable_sentry" {
  description = "Wire sentry-dsn-* secrets into the services (add versions first)."
  type        = bool
  default     = false
}

variable "enable_resend" {
  description = "Wire resend-api-key and EMAIL_FROM into the api, which mails invite codes (add the secret version first)."
  type        = bool
  default     = false
}

variable "email_from" {
  description = "Sender for invite emails, e.g. \"Firra <no-reply@example.com>\" (verified in Resend)."
  type        = string
  default     = ""
}

variable "worker_ingress" {
  description = "Worker ingress. Cloud Tasks counts as internal traffic for same-project targets."
  type        = string
  default     = "INGRESS_TRAFFIC_INTERNAL_ONLY"
}

variable "max_instances" {
  description = "Upper bound on Cloud Run instances per service."
  type        = object({ api = number, worker = number })
  default     = { api = 3, worker = 5 }
}

variable "enable_google" {
  description = "Wire the Google OAuth client (id below, secret in google-oauth-client-secret)."
  type        = bool
  default     = false
}

variable "google_client_id" {
  description = "Google OAuth client id for Gmail/Calendar connections (not a secret)."
  type        = string
  default     = ""
}

variable "enable_microsoft" {
  description = "Wire the Microsoft Entra app (id below, secret in microsoft-oauth-client-secret)."
  type        = bool
  default     = false
}

variable "microsoft_client_id" {
  description = "Microsoft Entra application (client) id for Outlook connections (not a secret)."
  type        = string
  default     = ""
}

variable "microsoft_tenant" {
  description = "Sign-in authority for delegated connections: organizations, common or a tenant id."
  type        = string
  default     = "organizations"
}

variable "vertex_location" {
  description = "Vertex AI location for Gemini calls: the EU multi-region or an EU region (the design notes)."
  type        = string
  default     = "eu"
  validation {
    condition     = var.vertex_location == "eu" || startswith(var.vertex_location, "europe-")
    error_message = "Only EU Vertex AI locations are allowed (CLAUDE.md section 7)."
  }
}

variable "llm_models" {
  description = "Gemini models per purpose; confirmed on the EU endpoint by the diagnostics.llm_probe task."
  type        = object({ extract = string, synth = string, embed = string })
  default = {
    extract = "gemini-3.5-flash-lite"
    synth   = "gemini-3.8-flash"
    embed   = "gemini-embedding-001"
  }
}

variable "processing_enabled" {
  description = "Run extraction (process.records, real Gemini calls) on ingested records. Off by default: it spends money."
  type        = bool
  default     = false
}
