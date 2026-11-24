resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "Firra ${var.environment} alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
  depends_on = [google_project_service.apis]
}

resource "google_monitoring_uptime_check_config" "health" {
  for_each     = var.create_services ? toset(["api"]) : toset([])
  project      = var.project_id
  display_name = "firra-${each.key} /health"
  timeout      = "10s"
  period       = "300s"
  # Google requires at least three probe locations. Probes only fetch /health (no data).
  selected_regions = ["EUROPE", "USA_VIRGINIA", "USA_OREGON"]

  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = trimprefix(local.run_url[each.key], "https://")
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  for_each     = google_monitoring_uptime_check_config.health
  project      = var.project_id
  display_name = "firra-${each.key} is down"
  combiner     = "OR"

  conditions {
    display_name = "uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${each.value.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.project_id", "resource.label.host"]
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account_id
  display_name    = "firra-${var.environment} monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.5, 0.8, 1.0]
    content {
      threshold_percent = threshold_rules.value
    }
  }
  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = false
  }
  depends_on = [google_project_service.apis]
}
