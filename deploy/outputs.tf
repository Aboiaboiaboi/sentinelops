output "api_url" {
  description = "Where the API is served. Firebase Hosting rewrites /api here."
  value       = google_cloud_run_v2_service.api.uri
}

output "reports_bucket" {
  description = "Set as STORAGE_BUCKET on the API. A name, not a URL."
  value       = google_storage_bucket.reports.name
}

output "artifact_registry" {
  description = "Where images must live for Cloud Run to pull them. GHCR is not accepted."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "deployer_service_account" {
  description = "Impersonated by GitHub Actions. Goes in the deploy workflow, not in a secret."
  value       = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "The full provider name google-github-actions/auth needs."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "migrate_job" {
  description = "Executed by the deploy workflow before a new revision takes traffic."
  value       = google_cloud_run_v2_job.migrate.name
}

output "database_private_ip" {
  description = "For a psql session from inside the VPC. There is no public address."
  value       = google_sql_database_instance.main.private_ip_address
}

output "redis_host" {
  description = "Private address of the Memorystore instance."
  value       = google_redis_instance.main.host
}
