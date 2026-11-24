# Services start from a placeholder image; GitHub Actions rolls out real images and Terraform
# ignores the image afterwards. Everything else (env, identity, scaling, ingress) lives here.

locals {
  placeholder_image = "us-docker.pkg.dev/cloudrun/container/hello"
  services          = var.create_services ? toset(["api", "worker"]) : toset([])

  plain_env = {
    api = merge(
      {
        FIRRA_ENV                 = var.environment
        SUPABASE_URL              = var.supabase_url
        SUPABASE_PUBLISHABLE_KEY  = var.supabase_publishable_key
        API_URL                   = local.run_url.api
        WORKER_URL                = local.run_url.worker
        TASK_QUEUE_MODE           = "cloud_tasks"
        GCP_PROJECT_ID            = var.project_id
        GCP_REGION                = var.region
        TASKS_INVOKER_SA_EMAIL    = google_service_account.tasks_invoker.email
        INTERNAL_CALLER_SA_EMAILS = google_service_account.deployer.email
        KMS_KEY_NAME              = google_kms_crypto_key.token_kek.id
        KMS_MODE                  = "cloud"
        GOOGLE_CLIENT_ID          = var.google_client_id
        MICROSOFT_CLIENT_ID       = var.microsoft_client_id
        MICROSOFT_TENANT          = var.microsoft_tenant
        RAW_BUCKET                = google_storage_bucket.raw.name
        # Shown to owners for Google Workspace domain-wide delegation (worker signs as this SA).
        WORKER_SA_CLIENT_ID = google_service_account.runtime["worker"].unique_id
      },
      var.enable_google ? { GMAIL_PUSH_SA_EMAIL = google_service_account.pubsub_push.email } : {},
      var.enable_resend ? { EMAIL_FROM = var.email_from } : {},
    )
    worker = merge(
      {
        FIRRA_ENV                = var.environment
        FIRRA_TASK_AUTH_MODE     = "oidc"
        FIRRA_TASK_OIDC_AUDIENCE = local.run_url.worker
        FIRRA_TASK_INVOKER_EMAIL = google_service_account.tasks_invoker.email
        FIRRA_KMS_MODE           = "cloud"
        FIRRA_KMS_KEY_NAME       = google_kms_crypto_key.token_kek.id
        FIRRA_RAW_BUCKET         = google_storage_bucket.raw.name
        FIRRA_GCP_PROJECT_ID     = var.project_id
        FIRRA_GCP_REGION         = var.region
        # Domain-wide delegation signs assertions as this account (keyless, via signJwt).
        FIRRA_WORKER_SERVICE_ACCOUNT = google_service_account.runtime["worker"].email
        FIRRA_GOOGLE_CLIENT_ID       = var.google_client_id
        FIRRA_MICROSOFT_CLIENT_ID    = var.microsoft_client_id
        # Vertex AI Gemini, EU data residency (the design notes).
        FIRRA_VERTEX_LOCATION = var.vertex_location
        FIRRA_MODEL_EXTRACT   = var.llm_models.extract
        FIRRA_MODEL_SYNTH     = var.llm_models.synth
        FIRRA_MODEL_EMBED     = var.llm_models.embed
        # Off until the owner approves LLM spend on this environment's records.
        FIRRA_PROCESSING_ENABLED = tostring(var.processing_enabled)
      },
      var.enable_google ? { FIRRA_GMAIL_PUSH_TOPIC = google_pubsub_topic.gmail_push[0].id } : {},
      var.enable_microsoft ? {
        FIRRA_MICROSOFT_NOTIFICATION_URL = "${local.run_url.api}/webhooks/microsoft"
      } : {},
    )
  }

  secret_env = {
    api = merge(
      { SUPABASE_SECRET_KEY = "supabase-secret-key" },
      var.enable_resend ? { RESEND_API_KEY = "resend-api-key" } : {},
      var.enable_google ? { GOOGLE_CLIENT_SECRET = "google-oauth-client-secret" } : {},
      var.enable_microsoft ? { MICROSOFT_CLIENT_SECRET = "microsoft-oauth-client-secret" } : {},
      var.enable_sentry ? { SENTRY_DSN = "sentry-dsn-api" } : {},
    )
    worker = merge(
      { FIRRA_DATABASE_URL = "worker-database-url" },
      var.enable_google ? { FIRRA_GOOGLE_CLIENT_SECRET = "google-oauth-client-secret" } : {},
      var.enable_microsoft ? {
        FIRRA_MICROSOFT_CLIENT_SECRET = "microsoft-oauth-client-secret"
      } : {},
      var.enable_sentry ? { FIRRA_SENTRY_DSN = "sentry-dsn-worker" } : {},
    )
  }

  run_settings = {
    api    = { ingress = "INGRESS_TRAFFIC_ALL", concurrency = 80, memory = "512Mi", timeout = "60s" }
    worker = { ingress = var.worker_ingress, concurrency = 10, memory = "1Gi", timeout = "600s" }
  }
}

resource "google_cloud_run_v2_service" "svc" {
  for_each            = local.services
  project             = var.project_id
  location            = var.region
  name                = "firra-${each.key}"
  ingress             = local.run_settings[each.key].ingress
  deletion_protection = var.environment == "prod"
  labels              = local.labels

  template {
    service_account                  = google_service_account.runtime[each.key].email
    max_instance_request_concurrency = local.run_settings[each.key].concurrency
    timeout                          = local.run_settings[each.key].timeout

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances[each.key]
    }

    containers {
      image = local.placeholder_image

      resources {
        limits = {
          cpu    = "1"
          memory = local.run_settings[each.key].memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.plain_env[each.key]
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env[each.key]
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [google_secret_manager_secret_iam_member.access]
}

# the api is public (it authenticates users itself); the worker only accepts
# requests from the Cloud Tasks invoker identity.
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = var.create_services ? toset(["api"]) : toset([])
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.svc[each.key].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  count    = var.create_services ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.svc["worker"].name
  role     = "roles/run.invoker"
  member   = google_service_account.tasks_invoker.member
}
