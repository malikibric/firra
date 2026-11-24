# Gmail push: Gmail publishes {emailAddress, historyId} to this topic; Pub/Sub pushes it to the api
# with an OIDC token of the push service account. Messages persist in the EU region only.

resource "google_pubsub_topic" "gmail_push" {
  count   = var.enable_google ? 1 : 0
  project = var.project_id
  name    = "gmail-push"
  labels  = local.labels

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
  message_retention_duration = "86400s"
  depends_on                 = [google_project_service.apis]
}

# Gmail's own publisher identity (fixed by Google). Domain-restricted sharing org policies may need
# an exception for it (see docs/runbooks/staging-setup.md).
resource "google_pubsub_topic_iam_member" "gmail_publisher" {
  count   = var.enable_google ? 1 : 0
  project = var.project_id
  topic   = google_pubsub_topic.gmail_push[0].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
}

resource "google_service_account" "pubsub_push" {
  project      = var.project_id
  account_id   = "firra-pubsub-push"
  display_name = "Pub/Sub push OIDC identity for api webhooks"
  depends_on   = [google_project_service.apis]
}

resource "google_pubsub_subscription" "gmail_push" {
  count                      = var.enable_google ? 1 : 0
  project                    = var.project_id
  name                       = "gmail-push-api"
  topic                      = google_pubsub_topic.gmail_push[0].id
  labels                     = local.labels
  ack_deadline_seconds       = 20
  message_retention_duration = "3600s" # older wake-ups are useless: the sweep has run by then

  push_config {
    push_endpoint = "${local.run_url.api}/webhooks/gmail"
    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
      audience              = "${local.run_url.api}/webhooks/gmail"
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  expiration_policy {
    ttl = "" # never expires
  }
}
