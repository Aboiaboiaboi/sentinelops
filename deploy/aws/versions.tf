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

# The reports bucket (storage.tf) is pinned here regardless of var.region.
# S3 bucket names are globally unique across every region and account, and
# the bucket already exists in us-east-1 — switching the default provider's
# region (done for the compute side, deploy/aws/README.md's region-migration
# note) would otherwise make Terraform believe the bucket needs creating
# fresh in the new region, which either fails outright (name taken) or, worse,
# succeeds in a confusing state. Moving real data to a new bucket in a new
# region is a deliberate migration this alias does not attempt.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}
