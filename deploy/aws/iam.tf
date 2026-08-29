# One role, scoped to exactly the bucket this deployment writes reports to.
# No AWS access key is ever generated or stored on the instance — boto3
# resolves credentials from the instance's attached role via the metadata
# service, the same "nothing here to leak because nothing here is a secret"
# property `deploy/iam.tf` gives each GCP service account.

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "sentinelops-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

data "aws_iam_policy_document" "s3_access" {
  # PutObject and GetObject on the objects themselves.
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.reports.arn}/*"]
  }

  # ListBucket on the bucket, separately — not an oversight duplicated from
  # the object statement. A GetObject for a key that does not exist yet (the
  # cache-miss path every report render starts with) needs ListBucket to get
  # back a clean NoSuchKey; without it S3 cannot safely tell the caller
  # whether the object exists at all and answers AccessDenied instead. Found
  # by hitting exactly that 500 on the first real deploy — see
  # deploy/aws/README.md.
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.reports.arn]
  }
}

resource "aws_iam_role_policy" "s3_access" {
  name   = "sentinelops-s3-access"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.s3_access.json
}

resource "aws_iam_instance_profile" "ec2" {
  name = "sentinelops-ec2-profile"
  role = aws_iam_role.ec2.name
}

# Lets the instance register with Systems Manager, so it can be reached via
# `aws ssm start-session` with no open inbound port and no IP allowlist at
# all — the agent on the instance connects outbound to AWS, and the caller
# authenticates by IAM rather than by network reachability. This is what
# makes personal access resilient to a changing home IP, unlike
# ssh_allowed_cidr in network.tf, which has to be updated by hand whenever it
# changes. SSH stays available as a second path — this is additive, not a
# replacement — and the AWS managed policy is used as-is rather than
# hand-rolled, since it is exactly the narrow, well-audited permission set
# the agent needs and nothing more.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
