# One queue per pipeline stage. Task handlers are idempotent; retries are bounded here and in
# job.max_attempts. Task-level logging is off (payloads are ids only, but keep logs minimal).
resource "google_cloud_tasks_queue" "stage" {
  for_each = toset(local.queues)
  project  = var.project_id
  location = var.region
  name     = each.key

  rate_limits {
    max_dispatches_per_second = 10
    max_concurrent_dispatches = 20
  }
  retry_config {
    max_attempts  = 10
    min_backoff   = "10s"
    max_backoff   = "600s"
    max_doublings = 4
  }
  stackdriver_logging_config {
    sampling_ratio = 0
  }
  depends_on = [google_project_service.apis]
}
