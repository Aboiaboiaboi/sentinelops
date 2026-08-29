# Every input, with why the default is the default — the same convention
# `deploy/variables.tf` sets for the GCP configuration.

variable "region" {
  description = "AWS region. us-east-1 has the widest service availability and is usually cheapest."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = <<-EOT
    t3.small (2 GB RAM), not the free-tier t3.micro (1 GB). Chosen with room
    to run the observability stack (Prometheus, Grafana, Loki, Tempo)
    alongside the app later without a resize — see 11-phase5-handoff.md's
    successor document on the metrics phase. ~$15/month.
  EOT
  type        = string
  default     = "t3.small"
}

variable "root_volume_size_gb" {
  description = "EBS root volume size. 20 GB covers the OS, five Docker images and the Trivy/Semgrep cache volume (~1.1 GB) with headroom."
  type        = number
  default     = 20
}

variable "ssh_allowed_cidr" {
  description = <<-EOT
    The one address (or range) allowed to reach port 22. Never 0.0.0.0/0 — SSH
    open to the internet is a credential-stuffing target the moment the
    instance exists. Set to your own current public IP; it changes if your ISP
    reassigns it, in which case `terraform apply` after updating this value is
    the fix, not a support ticket.
  EOT
  type        = string
}

variable "ssh_public_key" {
  description = <<-EOT
    The public half of the key pair used to reach the instance. Terraform
    manages the AWS-side key pair *object* (the name EC2 knows), never a
    private key — that is generated once, downloaded, and is yours to keep
    outside version control. See README.md for how the key currently in use
    was created.
  EOT
  type        = string
}

variable "storage_bucket_name" {
  description = <<-EOT
    Globally unique, so it is not left to a default. The convention used here
    is <project>-reports-<account-id>, which is unique without needing a
    random suffix and makes which account owns it legible from the name alone.
  EOT
  type        = string
}
