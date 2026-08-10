# Postgres, Redis, the reports bucket, and the image registry.
#
# The first two are the most portable things in this directory. They are a
# connection string each, and RDS, Neon, Upstash or a container substitutes for
# either without the application noticing — which is why the portability tiers
# in the README call them substitutable rather than locked in.

resource "google_sql_database_instance" "main" {
  name             = "sentinelops"
  region           = var.region
  database_version = "POSTGRES_17"

  # A populated database must not disappear because a block was deleted from a
  # file. `terraform destroy` still removes it, deliberately and after being
  # asked twice — this guards the accident, not the decision.
  deletion_protection = true

  depends_on = [google_service_networking_connection.private_services]

  settings {
    tier = var.db_tier
    # ZONAL, not REGIONAL. Regional doubles the cost for a standby this project
    # does not need; a portfolio deployment can survive a zone outage by being
    # briefly down.
    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      # The whole point of network.tf. No public address at all, so the instance
      # is not reachable from the internet by anyone holding the password.
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.main.id
      enable_private_path_for_google_cloud_services = true
    }

    database_flags {
      # The pool is 15 connections per API instance (pool_size 5 + overflow 10),
      # and api_max_instances bounds the replicas. The default on a shared-core
      # tier is 25, which two replicas would exhaust — and an exhausted
      # connection limit surfaces as intermittent 500s under load, not as a
      # clear error. 100 covers 5 API replicas, 3 workers, and the migration
      # job, with room left for a psql session.
      name  = "max_connections"
      value = "100"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = false
      start_time                     = "03:00"
    }
  }
}

resource "google_sql_database" "main" {
  name     = "sentinelops"
  instance = google_sql_database_instance.main.name
}

resource "random_password" "db" {
  length = 32
  # Symbols excluded on purpose. This password goes into a URL, and an
  # unescaped `@` or `/` in the userinfo section silently changes which host
  # the driver connects to. 32 alphanumeric characters is ~190 bits.
  special = false
}

resource "google_sql_user" "app" {
  name     = "sentinelops"
  instance = google_sql_database_instance.main.name
  password = random_password.db.result
}

resource "google_redis_instance" "main" {
  name           = "sentinelops"
  region         = var.region
  memory_size_gb = var.redis_memory_gb
  # BASIC is a single node with no replica. Its failure mode is that queued jobs
  # are lost and the rate limiter forgets its counters — both recoverable, and
  # STANDARD_HA doubles the most expensive line here to avoid it.
  tier               = "BASIC"
  redis_version      = "REDIS_7_2"
  authorized_network = google_compute_network.main.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_storage_bucket" "reports" {
  # Bucket names are globally unique across every GCP customer, so the project
  # id is a prefix rather than decoration.
  name     = "${var.project_id}-reports"
  location = var.region

  # No object is ever served directly from this bucket — the API reads bytes and
  # returns them itself, under its own authentication. Public access here would
  # make every report readable by anyone who guessed a scan id.
  public_access_prevention    = "enforced"
  uniform_bucket_level_access = true

  lifecycle_rule {
    # These objects are a cache, not the user's data: the fingerprint in the key
    # means anything deleted is re-rendered identically on the next request. So
    # they expire, and the bucket does not grow forever.
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  # No versioning, for the same reason. Keeping old copies of a regenerable
  # cache entry is paying to store something nothing will ever read.
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "sentinelops"
  location      = var.region
  format        = "DOCKER"
  description   = "API and worker images, mirrored from GHCR by the deploy workflow."

  # This exists because **Cloud Run cannot pull from GHCR** — not an
  # authentication problem a credential solves, the registry simply is not
  # accepted. GHCR stays the public, portable copy; this is the one Cloud Run
  # deploys from. The security tool images (Gitleaks, Trivy, Semgrep) have to be
  # mirrored here too before the sandbox can run them, for the same reason.
}
