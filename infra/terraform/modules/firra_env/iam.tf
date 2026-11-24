# Runtime identities (least privilege) and the GitHub Actions deployer.

resource "google_service_account" "runtime" {
  for_each     = toset(["api", "worker"])
  project      = var.project_id
  account_id   = "firra-${each.key}"
  display_name = "Firra ${each.key} runtime"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "tasks_invoker" {
  project      = var.project_id
  account_id   = "firra-tasks-invoker"
  display_name = "Cloud Tasks OIDC identity for worker calls"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "firra-deployer"
  display_name = "GitHub Actions deployer (via Workload Identity Federation)"
  depends_on   = [google_project_service.apis]
}

# api (new work) and worker (continuations of long jobs) create tasks that carry the
# tasks-invoker OIDC identity.
resource "google_project_iam_member" "enqueue" {
  for_each = toset(["api", "worker"])
  project  = var.project_id
  role     = "roles/cloudtasks.enqueuer"
  member   = google_service_account.runtime[each.key].member
}

resource "google_service_account_iam_member" "acts_as_invoker" {
  for_each           = toset(["api", "worker"])
  service_account_id = google_service_account.tasks_invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.runtime[each.key].member
}

# Existing staging state used these names for the api bindings.
moved {
  from = google_project_iam_member.api_enqueue
  to   = google_project_iam_member.enqueue["api"]
}

moved {
  from = google_service_account_iam_member.api_acts_as_invoker
  to   = google_service_account_iam_member.acts_as_invoker["api"]
}

# Deployer: push images, roll out new revisions running as the runtime identities.
resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deployer.member
}

resource "google_project_iam_member" "deployer_run" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  for_each           = google_service_account.runtime
  service_account_id = each.value.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

# Domain-wide delegation: the worker signs delegation assertions as itself (no key files).
resource "google_service_account_iam_member" "worker_signs_jwt" {
  service_account_id = google_service_account.runtime["worker"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.runtime["worker"].member
}

# Vertex AI (Gemini) for extraction and synthesis.
resource "google_project_iam_member" "worker_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.runtime["worker"].member
}

# Weekly evals (GitHub Actions) call Vertex AI on synthetic data only; nothing else.
resource "google_service_account" "evals" {
  project      = var.project_id
  account_id   = "firra-evals"
  display_name = "GitHub Actions evals (Vertex AI on synthetic data)"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "evals_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.evals.member
}
