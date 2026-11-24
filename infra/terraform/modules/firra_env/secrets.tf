# Secret shells only: values are added out of band (gcloud secrets versions add), so they never
# enter Terraform state. Replicated in the EU region only.

locals {
  secrets = merge(
    {
      "supabase-secret-key" = ["api"]
      "worker-database-url" = ["worker"]
    },
    var.enable_resend ? { "resend-api-key" = ["api"] } : {},
    var.enable_google ? { "google-oauth-client-secret" = ["api", "worker"] } : {},
    var.enable_microsoft ? { "microsoft-oauth-client-secret" = ["api", "worker"] } : {},
    var.enable_sentry ? {
      # Kept without any reader: removing the key would destroy the secret and its versions.
      "sentry-dsn-web"    = []
      "sentry-dsn-api"    = ["api"]
      "sentry-dsn-worker" = ["worker"]
    } : {},
  )
  secret_access = flatten([
    for secret, services in local.secrets : [for s in services : { secret = secret, service = s }]
  ])
}

resource "google_secret_manager_secret" "app" {
  for_each  = local.secrets
  project   = var.project_id
  secret_id = each.key
  labels    = local.labels

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = { for a in local.secret_access : "${a.secret}:${a.service}" => a }
  project   = var.project_id
  secret_id = google_secret_manager_secret.app[each.value.secret].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.runtime[each.value.service].member
}
