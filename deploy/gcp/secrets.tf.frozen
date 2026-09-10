# Secret Manager.
#
# Two of these Terraform generates and stores; one it creates empty and leaves
# for a human to fill. The distinction is the point: a value Terraform generates
# is in the state file, and the state file is a copy of every secret it knows.
# The GitHub App private key can mint access to every installed user's
# repositories, so it never passes through here — the secret *container* is
# declared, and the version is added out of band.

locals {
  # SQLAlchemy needs the +asyncpg driver named explicitly or it picks the
  # synchronous one and every await fails at runtime. Assembled here so the
  # application receives an ordinary Postgres URL and learns nothing about
  # where it is deployed.
  database_url = join("", [
    "postgresql+asyncpg://",
    google_sql_user.app.name, ":", random_password.db.result,
    "@", google_sql_database_instance.main.private_ip_address,
    ":5432/", google_sql_database.main.name,
  ])
}

resource "random_password" "secret_key" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret" "secret_key" {
  secret_id = "sentinelops-secret-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "secret_key" {
  secret      = google_secret_manager_secret.secret_key.id
  secret_data = random_password.secret_key.result
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "sentinelops-database-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = local.database_url
}

# Declared empty. `gcloud secrets versions add sentinelops-github-app-key
# --data-file=-` adds the value, base64-encoded as the application expects,
# without it ever entering Terraform state or a shell history that records
# arguments.
resource "google_secret_manager_secret" "github_app_key" {
  secret_id = "sentinelops-github-app-key"
  replication {
    auto {}
  }
}

# Read access, granted per secret rather than project-wide. The worker never
# renders a report and the API never clones, but both need the database — so the
# split is by secret, not by convenience.
resource "google_secret_manager_secret_iam_member" "api" {
  for_each = {
    secret_key     = google_secret_manager_secret.secret_key.id
    database_url   = google_secret_manager_secret.database_url.id
    github_app_key = google_secret_manager_secret.github_app_key.id
  }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "worker" {
  for_each = {
    database_url   = google_secret_manager_secret.database_url.id
    github_app_key = google_secret_manager_secret.github_app_key.id
  }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}
