variable "project_id" {
  type = string
}

variable "billing_account_id" {
  type = string
}

variable "budget_amount" {
  type = number
}

variable "budget_currency" {
  type    = string
  default = "USD"
}

variable "alert_email" {
  type = string
}

variable "supabase_url" {
  type = string
}

variable "supabase_publishable_key" {
  type = string
}

variable "create_services" {
  type    = bool
  default = true
}

variable "enable_sentry" {
  type    = bool
  default = false
}

variable "enable_resend" {
  type    = bool
  default = false
}

variable "email_from" {
  type    = string
  default = ""
}

variable "enable_google" {
  type    = bool
  default = false
}

variable "google_client_id" {
  type    = string
  default = ""
}

variable "enable_microsoft" {
  type    = bool
  default = false
}

variable "microsoft_client_id" {
  type    = string
  default = ""
}
