# Key-encryption key for per-workspace data keys that protect OAuth tokens (used from P1).
resource "google_kms_key_ring" "firra" {
  project    = var.project_id
  name       = "firra"
  location   = var.region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "token_kek" {
  name            = "token-kek"
  key_ring        = google_kms_key_ring.firra.id
  rotation_period = "7776000s" # 90 days
  labels          = local.labels

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "token_kek_users" {
  for_each      = toset(["api", "worker"])
  crypto_key_id = google_kms_crypto_key.token_kek.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_service_account.runtime[each.key].member
}
