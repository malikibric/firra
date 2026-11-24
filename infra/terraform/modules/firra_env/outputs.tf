output "urls" {
  description = "Cloud Run service URLs."
  value       = local.run_url
}

output "artifact_registry" {
  description = "Docker repository for images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "workload_identity_provider" {
  description = "GitHub variable WIF_PROVIDER."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  description = "GitHub variable GCP_DEPLOYER_SA."
  value       = google_service_account.deployer.email
}

output "tasks_invoker_service_account" {
  value = google_service_account.tasks_invoker.email
}

output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "evals_service_account" {
  description = "GitHub variable GCP_EVALS_SA (weekly evals)."
  value       = google_service_account.evals.email
}
