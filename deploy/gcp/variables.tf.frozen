variable "project_id" {
  description = "The GCP project everything is created in. Created by bootstrap.sh."
  type        = string
}

variable "region" {
  description = "Single region. Multi-region is out of scope for this phase."
  type        = string
  default     = "us-central1"
}

variable "github_repository" {
  description = <<-EOT
    owner/repo, used to scope the Workload Identity Federation binding.

    This string is the entire security boundary on the deploy credential: it is
    what stops a workflow in *any other* GitHub repository from minting a token
    for this project's service account. Getting it wrong in the permissive
    direction is not a typo, it is an open door.
  EOT
  type        = string
}

variable "api_image" {
  description = <<-EOT
    Full image reference for the API, including the digest.

    A digest, not a tag: a tag can be moved, and then "what is deployed" is a
    question nobody can answer after the fact. The CI publish job prints the
    digest into its run summary.
  EOT
  type        = string
  default     = ""
}

variable "worker_image" {
  description = "Full image reference for the worker, including the digest."
  type        = string
  default     = ""
}

variable "db_tier" {
  description = <<-EOT
    Cloud SQL machine type. db-f1-micro is ~$9/month and is a shared-core
    instance — fine for everything up to the load test, and the first thing to
    raise when the k6 numbers say the database is the ceiling rather than the
    application.
  EOT
  type        = string
  default     = "db-f1-micro"
}

variable "redis_memory_gb" {
  description = <<-EOT
    Memorystore size. The single most expensive line in this directory at
    roughly $35/month, and the reason `terraform destroy` between working
    sessions is part of the workflow rather than an emergency.
  EOT
  type        = number
  default     = 1
}

variable "api_max_instances" {
  description = <<-EOT
    Ceiling on API replicas. A ceiling is a cost control, not a capacity plan:
    each instance opens up to 15 database connections, so this number times 15
    must stay under what Cloud SQL will accept. See max_connections in
    data_stores.tf.
  EOT
  type        = number
  default     = 5
}

variable "worker_max_instances" {
  description = "Ceiling on worker replicas. Each one consumes the same Redis queue."
  type        = number
  default     = 3
}

variable "monthly_budget" {
  description = "Budget alert threshold in USD. Alerts only — it cannot stop spending."
  type        = number
  default     = 60
}

variable "billing_account" {
  description = "Billing account id, for the budget alert. Empty disables the budget."
  type        = string
  default     = ""
}

variable "frontend_url" {
  description = <<-EOT
    Where the SPA is served. The API uses it for the GitHub App install redirect
    and for its CORS allowlist.

    Empty until Firebase Hosting exists, which is milestone 6 — and it is
    deliberately not created here, because the single-origin design means the
    SPA rewrites /api to this service rather than the two talking cross-origin.
  EOT
  type        = string
  default     = ""
}
