# Scheduled maintenance calls to the worker, authenticated like Cloud Tasks (tasks-invoker OIDC).

resource "google_cloud_scheduler_job" "purge_bodies" {
  count       = var.create_services ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "purge-bodies"
  description = "Body retention: clear message bodies and raw-object references once processed and delivered (72 h / 7 days caps)."
  schedule    = "15 * * * *"
  time_zone   = "Etc/UTC"

  attempt_deadline = "600s"
  retry_config {
    retry_count          = 3
    min_backoff_duration = "60s"
    max_backoff_duration = "600s"
  }

  http_target {
    http_method = "POST"
    uri         = "${local.run_url.worker}/maintenance/purge-bodies"
    oidc_token {
      service_account_email = google_service_account.tasks_invoker.email
      audience              = local.run_url.worker
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_scheduler_job" "sync_sweep" {
  count       = var.create_services ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "sync-sweep"
  description = "Incremental sync for every active connection (push notifications are best effort)."
  schedule    = "*/15 * * * *"
  time_zone   = "Etc/UTC"

  attempt_deadline = "300s"
  retry_config {
    retry_count          = 1
    min_backoff_duration = "30s"
  }

  http_target {
    http_method = "POST"
    uri         = "${local.run_url.worker}/maintenance/sweep"
    oidc_token {
      service_account_email = google_service_account.tasks_invoker.email
      audience              = local.run_url.worker
    }
  }

  depends_on = [google_project_service.apis]
}
