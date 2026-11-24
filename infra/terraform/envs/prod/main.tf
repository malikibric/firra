terraform {
  required_version = ">= 1.9"
  # State bucket is created once by hand (docs/runbooks/staging-setup.md):
  #   terraform init -backend-config="bucket=<project_id>-tfstate"
  backend "gcs" {
    prefix = "firra/prod"
  }
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.4"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = "europe-west3"
  # Budgets API calls are billed/quota'd against this project.
  user_project_override = true
  billing_project       = var.project_id
}

module "env" {
  source = "../../modules/firra_env"

  project_id               = var.project_id
  environment              = "prod"
  billing_account_id       = var.billing_account_id
  budget_amount            = var.budget_amount
  budget_currency          = var.budget_currency
  alert_email              = var.alert_email
  github_repository        = "malikibric/fira"
  github_repository_id     = "1390625796"
  github_environment       = "production"
  supabase_url             = var.supabase_url
  supabase_publishable_key = var.supabase_publishable_key
  create_services          = var.create_services
  enable_sentry            = var.enable_sentry
  enable_resend            = var.enable_resend
  email_from               = var.email_from
  enable_google            = var.enable_google
  google_client_id         = var.google_client_id
  enable_microsoft         = var.enable_microsoft
  microsoft_client_id      = var.microsoft_client_id
}

output "urls" {
  value = module.env.urls
}

output "artifact_registry" {
  value = module.env.artifact_registry
}

output "workload_identity_provider" {
  value = module.env.workload_identity_provider
}

output "deployer_service_account" {
  value = module.env.deployer_service_account
}

output "evals_service_account" {
  value = module.env.evals_service_account
}
