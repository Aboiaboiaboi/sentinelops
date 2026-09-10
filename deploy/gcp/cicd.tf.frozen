# How GitHub Actions authenticates to this project.
#
# Workload Identity Federation, so there is no key. The alternative — a service
# account JSON key pasted into a repository secret — is a credential that never
# expires, works from anywhere on the internet, and is the single most common
# way a GCP project ends up mining cryptocurrency for somebody else. Here,
# GitHub's OIDC token is exchanged for a short-lived access token, and the
# exchange only succeeds for a workflow running in one named repository.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # The second half of the boundary, and the one that is easy to leave out.
  # Without a condition, *any* GitHub repository anywhere can present a token to
  # this provider — the attribute mapping alone only records which repository
  # asked, it does not restrict who may. Google now rejects a provider with no
  # condition, which is a rare case of a default being safe.
  attribute_condition = "assertion.repository == '${var.github_repository}'"
}

# And the third half: the pool may exchange tokens, but only a principal whose
# repository attribute matches may impersonate the deployer. Belt and braces on
# purpose — this is the credential that can replace what production runs.
resource "google_service_account_iam_member" "github_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member = join("", [
    "principalSet://iam.googleapis.com/",
    google_iam_workload_identity_pool.github.name,
    "/attribute.repository/",
    var.github_repository,
  ])
}
