# The things that actually run: the API service, the worker pool, and the
# migration job.
#
# All three are given `min instances = 0`. A single always-warm instance is
# roughly $13/month for the privilege of not cold-starting a demo nobody is
# currently looking at, and the load test measures the warm path regardless.
# Cold start is the right trade here and it is a deliberate one.

locals {
  # Direct VPC egress, not a Serverless VPC Access connector. The connector is
  # the older mechanism and bills for two always-on e2-micro instances whether
  # or not anything is talking to the database; direct egress allocates an
  # address per running instance out of the subnet and costs nothing when the
  # service is scaled to zero.
  #
  # PRIVATE_RANGES_ONLY, not ALL_TRAFFIC: only traffic to RFC1918 addresses —
  # Cloud SQL and Memorystore — goes through the VPC. Anything bound for the
  # internet takes the normal path, so this does not need a Cloud NAT to exist,
  # which would be another always-on charge.
  egress = "PRIVATE_RANGES_ONLY"

  # Settings that describe the deployment rather than the code, shared by every
  # process that runs it.
  common_env = {
    ENVIRONMENT = "production"
    REDIS_URL   = "redis://${google_redis_instance.main.host}:${google_redis_instance.main.port}/0"
  }
}

resource "google_cloud_run_v2_service" "api" {
  name     = "sentinelops-api"
  location = var.region
  # Public. Firebase Hosting rewrites /api to this service, and a rewrite is an
  # ordinary unauthenticated request from Google's edge — INGRESS_TRAFFIC_INTERNAL
  # would reject it. Authentication is the application's httpOnly cookie, which
  # is where it has always been.
  ingress = "INGRESS_TRAFFIC_ALL"

  # Cloud Run refuses to create a service whose image cannot be pulled, so this
  # is only applied once the publish job has pushed one. See var.api_image.
  deletion_protection = false

  template {
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = 0
      max_instance_count = var.api_max_instances
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = local.egress
    }

    containers {
      image = var.api_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        # CPU only while a request is in flight. The API does no background
        # work — rendering a report happens inside a request — so paying for
        # an idle allocated CPU buys nothing.
        cpu_idle = true
      }

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name  = "STORAGE_BUCKET"
        value = google_storage_bucket.reports.name
      }

      env {
        # Rate limiting counts per replica when this is memory://, so N replicas
        # means an effective limit of N times what was configured. Database 1,
        # because arq owns 0.
        name  = "RATE_LIMIT_STORAGE_URI"
        value = "redis://${google_redis_instance.main.host}:${google_redis_instance.main.port}/1"
      }

      env {
        # Behind Google's front end every request arrives from a local proxy, so
        # without this uvicorn refuses to read X-Forwarded-For and every user on
        # earth shares one rate-limit bucket. Never "*" on a public service:
        # uvicorn would then trust an attacker-supplied header, and rotating it
        # bypasses the limit entirely. Milestone 6 verifies this from two
        # addresses rather than trusting that the value is right.
        name  = "FORWARDED_ALLOW_IPS"
        value = "169.254.1.1"
      }

      env {
        name  = "FRONTEND_URL"
        value = var.frontend_url
      }

      env {
        # Only consulted cross-origin, and the single-origin design means it
        # should never be. Set anyway, so that if the SPA is ever served from
        # its own domain the failure is a CORS error naming this variable rather
        # than a silent one.
        name  = "CORS_ORIGINS"
        value = jsonencode(compact([var.frontend_url]))
      }

      dynamic "env" {
        for_each = {
          SECRET_KEY   = google_secret_manager_secret.secret_key.secret_id
          DATABASE_URL = google_secret_manager_secret.database_url.secret_id
          # Base64-encoded, because a multi-line PEM does not survive
          # environment injection.
          GITHUB_APP_PRIVATE_KEY_B64 = google_secret_manager_secret.github_app_key.secret_id
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret = env.value
              # "latest", so rotating a secret is adding a version and
              # redeploying rather than editing infrastructure.
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        # Traffic is withheld until /health answers. Without a probe Cloud Run
        # considers the container ready as soon as it binds the port, which for
        # this app is before the lifespan handler has connected to Redis — so
        # the first requests of every cold start would fail.
        http_get {
          path = "/health"
        }
        initial_delay_seconds = 3
        period_seconds        = 3
        failure_threshold     = 10
      }
    }
  }

  # Only ever one live revision taking traffic. Milestone 5 promotes explicitly
  # after the health check passes rather than letting a broken revision take
  # traffic the moment it is created.
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_secret_manager_secret_version.secret_key,
    google_secret_manager_secret_version.database_url,
  ]
}

# Public invocation. The service is the API behind the SPA; requiring an IAM
# token here would mean the browser cannot call it at all.
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# The worker consumes a Redis queue and serves no HTTP, which is exactly what a
# Worker Pool is for: no request-based scaling, no port to expose, no URL, and
# roughly 40% cheaper than a Service for long-running work.
resource "google_cloud_run_v2_worker_pool" "worker" {
  name     = "sentinelops-worker"
  location = var.region

  deletion_protection = false

  # Outside `template`, unlike the Service — where scaling sits inside it. The
  # difference is that a Service scales per revision and a Worker Pool scales
  # the pool, and the schema says so. Found by validating rather than by
  # assuming the two resources were shaped alike.
  scaling {
    # Manual, because a Worker Pool has no request signal to scale on and does
    # not read queue depth. Milestone 7's queue-depth metric is what would make
    # an autoscaling rule possible; until then this is a fixed count.
    scaling_mode          = "MANUAL"
    manual_instance_count = var.worker_max_instances
  }

  template {
    service_account = google_service_account.worker.email

    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = local.egress
    }

    containers {
      image = var.worker_image

      resources {
        limits = {
          cpu = "2"
          # Scanners hold a repository index in memory and the three security
          # tools run concurrently. 2Gi is the Phase 3 measurement, not a guess.
          memory = "2Gi"
        }
      }

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        # No sandbox implementation exists for Cloud Run yet, so the worker runs
        # with NullSandbox and the three tool-backed checks report `errored`.
        # That is the honest outcome and it is visible in the UI. Milestone 3
        # replaces it.
        name  = "SANDBOX_ENABLED"
        value = "false"
      }

      dynamic "env" {
        for_each = {
          DATABASE_URL               = google_secret_manager_secret.database_url.secret_id
          GITHUB_APP_PRIVATE_KEY_B64 = google_secret_manager_secret.github_app_key.secret_id
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_version.database_url]
}

# Migrations run here, as one job, exactly once per deploy — not on container
# start. Every replica running `alembic upgrade head` at boot is a race for a
# lock: Alembic holds one, so the realistic outcome is not corruption but every
# replica but one timing out during a deploy, which is an outage either way.
resource "google_cloud_run_v2_job" "migrate" {
  name     = "sentinelops-migrate"
  location = var.region

  deletion_protection = false

  template {
    template {
      service_account = google_service_account.api.email
      # No retries. A failed migration should stop the deploy and be read, not
      # be attempted twice against a database it may have half-changed.
      max_retries = 0
      timeout     = "600s"

      vpc_access {
        network_interfaces {
          network    = google_compute_network.main.id
          subnetwork = google_compute_subnetwork.main.id
        }
        egress = local.egress
      }

      containers {
        # The API image, which already carries alembic/ and alembic.ini.
        image   = var.api_image
        command = ["alembic"]
        args    = ["upgrade", "head"]

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_version.database_url]
}
