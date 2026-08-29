# Object storage for rendered PDF reports. S3Storage (backend/app/utils/storage.py)
# is what reads and writes this bucket — see the portability table in the main
# README for why the same class also works against R2, Spaces and MinIO.

resource "aws_s3_bucket" "reports" {
  bucket = var.storage_bucket_name
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
