# Object storage for rendered PDF reports. S3Storage (backend/app/utils/storage.py)
# is what reads and writes this bucket — see the portability table in the main
# README for why the same class also works against R2, Spaces and MinIO.

# Pinned to the us-east-1 provider alias (versions.tf) regardless of
# var.region — this bucket was created in us-east-1 and stays there. The
# instance reads/writes it over the network like any S3-compatible client
# would; a few tens of milliseconds of extra latency to Mumbai is not worth
# a real data migration to a new bucket.
resource "aws_s3_bucket" "reports" {
  provider = aws.us_east_1
  bucket   = var.storage_bucket_name
}

resource "aws_s3_bucket_public_access_block" "reports" {
  provider = aws.us_east_1
  bucket   = aws_s3_bucket.reports.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  provider = aws.us_east_1
  bucket   = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
