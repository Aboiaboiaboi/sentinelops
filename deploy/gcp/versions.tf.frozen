# Provider and state configuration.
#
# The state file is the only thing here that is not reproducible. It records
# which real resource each block in this directory corresponds to, so losing it
# means Terraform no longer knows that the Cloud SQL instance it is looking at
# is the one described below — and the next apply tries to create a second one.
# It lives in a bucket rather than on a laptop for that reason, and the bucket
# is created by bootstrap.sh because Terraform cannot create the bucket that
# holds its own state.

terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source = "hashicorp/google"
      # Pinned to a minor range rather than left open. A provider upgrade can
      # change a default and produce a plan that destroys and recreates
      # something on an apply nobody meant to be a migration.
      version = "~> 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Filled in by bootstrap.sh, which writes backend.hcl. Kept out of this file
  # so the bucket name — which depends on the project id — is not hardcoded
  # into version control.
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
