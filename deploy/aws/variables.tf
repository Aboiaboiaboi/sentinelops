# Every input, with why the default is the default — the same convention
# `deploy/variables.tf` sets for the GCP configuration.

variable "region" {
  description = <<-EOT
    ap-south-1 (Mumbai) — the closest mature AWS region to Lahore (~1,600km),
    with full service parity and comparable pricing to us-east-1. Moved from
    us-east-1 once the deployment's actual user location made the latency
    difference worth the one-time migration (new IP, new sslip.io domain,
    GitHub App URLs, DEPLOY_DOMAIN — see deploy/aws/README.md).
  EOT
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = <<-EOT
    c7i-flex.large (4 GB RAM), not t3.medium — t3.medium is blocked on this
    account by an AWS Free Tier usage restriction (RunInstances returns
    InvalidParameterCombination for anything outside the account's
    free-tier-eligible list: t3.micro/small, t4g.micro/small, c7i-flex.large,
    m7i-flex.large). c7i-flex.large matches t3.medium's 4GB and was the
    cheaper of the two allowed options with more RAM than t3.small
    (m7i-flex.large has 8GB but costs more). Resized up from t3.small once
    the observability stack (Prometheus, Grafana, Loki, node-exporter,
    cAdvisor, Promtail) was actually added — see
    deploy/compose/docker-compose.observability.yml. t3.small fit tightly
    (~1.35GB steady state of 2GB); this leaves genuine headroom for a scan
    burst during a live demo. ~$62/month if run 24/7 — far less in practice,
    since the instance is stopped between sessions.
  EOT
  type        = string
  default     = "c7i-flex.large"
}

variable "root_volume_size_gb" {
  description = <<-EOT
    EBS root volume size. 30 GB: the OS, eight Docker images (five app +
    Prometheus/Loki/Grafana/node-exporter/cAdvisor/Alloy), the
    Trivy/Semgrep cache (~1.1 GB), and 14 days of retained Prometheus +
    Loki data (~1.5 GB combined) — with headroom.
  EOT
  type        = number
  default     = 30
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
