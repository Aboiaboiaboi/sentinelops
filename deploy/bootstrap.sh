#!/usr/bin/env bash
#
# One-time setup, run by a human before the first `terraform apply`.
#
# Everything here is a chicken-and-egg problem that Terraform cannot solve for
# itself: it cannot create the project it authenticates against, enable the API
# it needs to enable APIs, or create the bucket that holds its own state. That
# is the entire contents of this file — anything that *can* be Terraform is
# Terraform, because a console click cannot be reviewed, diffed, or recreated
# after a teardown.
#
# Run it once:
#
#     ./bootstrap.sh my-project-id 01ABCD-234567-89EFGH
#
# It is safe to re-run. Every step checks before it creates.

set -euo pipefail

PROJECT_ID="${1:?usage: bootstrap.sh <project-id> [billing-account-id]}"
BILLING_ACCOUNT="${2:-}"
REGION="${REGION:-us-central1}"
STATE_BUCKET="${PROJECT_ID}-tfstate"

echo "==> Project: ${PROJECT_ID}   Region: ${REGION}"

if ! gcloud projects describe "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "==> Creating the project"
  gcloud projects create "${PROJECT_ID}"
else
  echo "==> Project already exists"
fi

if [[ -n "${BILLING_ACCOUNT}" ]]; then
  echo "==> Linking billing"
  # Nothing below this line works without it: every API enable on a project with
  # no billing account fails with a message about the API rather than about
  # billing, which is a genuinely confusing half hour.
  gcloud billing projects link "${PROJECT_ID}" --billing-account "${BILLING_ACCOUNT}"
else
  echo "!!! No billing account given. Link one before running terraform apply."
fi

echo "==> Enabling APIs"
# serviceusage and cloudresourcemanager first, because they are what lets
# Terraform enable or read anything else. compute and servicenetworking are the
# private network Cloud SQL and Memorystore attach to; iamcredentials is what
# actually mints the short-lived token during a Workload Identity exchange, and
# leaving it off produces a permission error that reads as if the trust policy
# were wrong.
gcloud services enable --project "${PROJECT_ID}" \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  compute.googleapis.com \
  servicenetworking.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  cloudbilling.googleapis.com \
  billingbudgets.googleapis.com

echo "==> State bucket: gs://${STATE_BUCKET}"
if ! gcloud storage buckets describe "gs://${STATE_BUCKET}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${STATE_BUCKET}" \
    --project "${PROJECT_ID}" \
    --location "${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention
  # Versioning on, and this is the one bucket where it matters. State is the
  # only thing in this directory that is not reproducible: it maps each block to
  # the real resource it created, and losing it means the next apply builds a
  # second copy of everything rather than recognising the first.
  gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning
else
  echo "==> State bucket already exists"
fi

cat > backend.hcl <<EOF
bucket = "${STATE_BUCKET}"
prefix = "terraform/state"
EOF

cat > terraform.tfvars <<EOF
project_id      = "${PROJECT_ID}"
region          = "${REGION}"
billing_account = "${BILLING_ACCOUNT}"
EOF

cat <<EOF

==> Done. Next:

    terraform init -backend-config=backend.hcl
    # add github_repository, api_image and worker_image to terraform.tfvars
    terraform plan

Read the plan before applying it. It creates a Cloud SQL instance and a
Memorystore instance, which bill from the moment they exist.

The GitHub App private key is deliberately not handled here or by Terraform —
it can mint access to every installed user's repositories, and a value
Terraform generates is a value in the state file. After the first apply:

    gcloud secrets versions add sentinelops-github-app-key --data-file=- \\
        --project ${PROJECT_ID}

then paste the base64-encoded key and press Ctrl-D. Piped through stdin rather
than passed as an argument, so it never reaches a shell history or a process
listing.
EOF
