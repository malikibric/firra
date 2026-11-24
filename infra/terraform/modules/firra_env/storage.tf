# Access logs for the data buckets (object names are ids only).
# nosemgrep: gcp-cloud-storage-logging -- the log sink cannot log to itself.
resource "google_storage_bucket" "access_logs" {
  project                     = var.project_id
  name                        = "${var.project_id}-access-logs"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.labels

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.apis]
}

# Cloud Storage writes usage logs as this group.
resource "google_storage_bucket_iam_member" "access_logs_writer" {
  bucket = google_storage_bucket.access_logs.name
  role   = "roles/storage.objectCreator"
  member = "group:cloud-storage-analytics@google.com"
}

# Raw provider payloads: backstop lifecycle at 7 days (the purge job deletes earlier), no soft-delete retention beyond that.
resource "google_storage_bucket" "raw" {
  project                     = var.project_id
  name                        = "${var.project_id}-raw"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.labels

  logging {
    log_bucket        = google_storage_bucket.access_logs.name
    log_object_prefix = "raw/"
  }
  soft_delete_policy {
    retention_duration_seconds = 0
  }
  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.apis]
}

# Nightly pg_dump target (job added later): 30-day retention.
resource "google_storage_bucket" "backups" {
  project                     = var.project_id
  name                        = "${var.project_id}-backups"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.labels

  logging {
    log_bucket        = google_storage_bucket.access_logs.name
    log_object_prefix = "backups/"
  }
  soft_delete_policy {
    retention_duration_seconds = 0
  }
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "worker_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.runtime["worker"].member
}

# The api opens upload sessions and checks uploaded sizes, for objects under uploads/ only.
resource "google_storage_bucket_iam_member" "api_uploads" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])
  bucket   = google_storage_bucket.raw.name
  role     = each.value
  member   = google_service_account.runtime["api"].member

  condition {
    title      = "uploads-only"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.raw.name}/objects/uploads/\")"
  }
}
