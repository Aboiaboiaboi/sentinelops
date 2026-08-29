# A narrowly-scoped identity for GitHub Actions to open and close SSH access
# for itself, and nothing else. Deploy.yml SSHes into the instance from a
# GitHub-hosted runner, whose IP is a different address from a large,
# unpredictable pool on every single run — there is no fixed CIDR to put in
# `ssh_allowed_cidr`, unlike a human's home IP. So instead of leaving SSH open
# to the world, or maintaining a list, the workflow discovers its own current
# IP at the start of each run, authorizes exactly that /32 on port 22,
# deploys, and revokes it again — SSH is closed to the internet the rest of
# the time, the same way it always has been for a human's own access.
#
# This identity can only touch this one security group's ingress rules. It
# cannot start, stop or describe the instance, read the S3 bucket, or do
# anything else in the account — the blast radius of the GitHub secret this
# grants access to is "can open and close a firewall port," not "can reach
# production."

resource "aws_iam_user" "ci_deploy" {
  name = "sentinelops-ci-deploy"
}

data "aws_iam_policy_document" "ci_deploy" {
  statement {
    actions   = ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"]
    resources = ["arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:security-group/${aws_security_group.web.id}"]
  }

  # DescribeSecurityGroups takes no resource-level permissions in IAM — it is
  # a read used only to confirm a rule was actually added/removed, granted
  # account-wide because EC2 offers no narrower scope for it, same as every
  # other Describe* action.
  statement {
    actions   = ["ec2:DescribeSecurityGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "ci_deploy" {
  name   = "sentinelops-ci-security-group-access"
  user   = aws_iam_user.ci_deploy.name
  policy = data.aws_iam_policy_document.ci_deploy.json
}

data "aws_caller_identity" "current" {}
