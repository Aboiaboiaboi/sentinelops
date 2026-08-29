# Provider and state configuration.
#
# Local state, deliberately — not the S3+DynamoDB remote backend `deploy/*.tf`
# would use for GCS. This directory describes six resources, not the forty-odd
# in the GCP configuration, and a bootstrap step to create a state bucket
# before Terraform can run costs more here than it saves. terraform.tfstate is
# gitignored for the same reason `deploy/*.tf`'s state was never committed: it
# is a local secret (nothing sensitive resides in it here, since credentials
# are never Terraform-generated — see iam.tf — but the state file is still the
# only record of which real resource each block below corresponds to).
#
# Revisit remote state if this ever needs a second person applying changes.

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Pinned to a minor range, the same reasoning `deploy/versions.tf` gives
      # for the google provider: an upgrade that changes a default should not
      # silently turn into a plan that destroys and recreates something.
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}
