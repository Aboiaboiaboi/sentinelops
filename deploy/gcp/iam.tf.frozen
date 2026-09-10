# Identities, and what each one is allowed to do.
#
# Three service accounts, not one. Cloud Run defaults to the Compute Engine
# default service account, which holds project Editor — so an unconfigured
# deployment gives the API permission to delete the database. Each account below
# gets only what its process actually calls, which means a compromise of any one
# of them is bounded by what that process was already able to do.

resource "google_service_account" "api" {
  account_id   = "sentinelops-api"
  display_name = "SentinelOps API"
}

resource "google_service_account" "worker" {
  account_id   = "sentinelops-worker"
  display_name = "SentinelOps worker"
}

resource "google_service_account" "deployer" {
  account_id   = "sentinelops-deployer"
  display_name = "GitHub Actions deployer"
}

# --- API ------------------------------------------------------------------
# Serves HTTP, renders reports, reads and writes the database. It never starts
# a container and never clones anything.

resource "google_project_iam_member" "api_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "api_reports" {
  bucket = google_storage_bucket.reports.name
  # objectAdmin, not admin: it reads, writes and overwrites objects, and has no
  # business changing the bucket's own policy.
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.api.email}"
}

# --- Worker ---------------------------------------------------------------
# Consumes the queue, clones repositories, and — once milestone 3 lands — starts
# sandbox executions. It writes scan results and never touches a report.

resource "google_project_iam_member" "worker_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_run_jobs" {
  project = var.project_id
  # The narrowest role that can execute a job. Not run.admin, which could also
  # rewrite the job definition — and a worker that can redefine the sandbox it
  # runs in is a worker that can remove its own isolation.
  role   = "roles/run.invoker"
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_logs_reader" {
  project = var.project_id
  # A Cloud Run Job does not hand stdout back to its caller; output goes to
  # Cloud Logging and is read back from there. Milestone 3's spike decides
  # whether that is how the tool output actually travels — if it turns out to
  # be a GCS object instead, this binding should go.
  role   = "roles/logging.viewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# --- Both -----------------------------------------------------------------

resource "google_project_iam_member" "log_writer" {
  for_each = {
    api    = google_service_account.api.email
    worker = google_service_account.worker.email
  }
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${each.value}"
}

resource "google_project_iam_member" "metric_writer" {
  for_each = {
    api    = google_service_account.api.email
    worker = google_service_account.worker.email
  }
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${each.value}"
}

# --- Deployer -------------------------------------------------------------
# Used by GitHub Actions through Workload Identity Federation. Broad by
# necessity — it replaces revisions — and therefore the account whose *trust
# policy* matters most. See cicd.tf.

resource "google_project_iam_member" "deployer" {
  for_each = toset([
    "roles/run.admin",
    "roles/artifactregistry.writer",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# Deploying a revision means telling Cloud Run to run it *as* the API or worker
# account, and GCP treats that as impersonation: without this the deploy fails
# with a permission error naming a service account rather than the service.
# Granted per-account rather than project-wide, so the deployer can act as these
# two and nothing else.
resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each = {
    api    = google_service_account.api.name
    worker = google_service_account.worker.name
  }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}
