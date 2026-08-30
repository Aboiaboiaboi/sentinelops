# The default VPC and one of its subnets, read rather than created.
#
# `deploy/network.tf` builds a dedicated VPC for the GCP deployment because
# that configuration also runs Cloud SQL and Memorystore on private IPs behind
# it — there is a real peering and egress story to get right. This deployment
# is one VM with a public IP, running everything in Docker on that one host;
# a dedicated VPC would add resources with nothing for them to isolate.
# Revisit if this ever grows a second instance or a managed database.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Pinned to one subnet rather than "any of them" — an instance's subnet
# determines its availability zone, and an apply that silently moved the
# instance to a different AZ on every run would be surprising. ap-south-1a is
# where the instance in use today actually runs.
data "aws_subnet" "app" {
  filter {
    name   = "availability-zone"
    values = ["${var.region}a"]
  }

  vpc_id = data.aws_vpc.default.id
}

resource "aws_security_group" "web" {
  name = "sentinelops-web"
  # A security group's description is immutable in AWS — changing this string
  # forces a full replacement (a new group, a window with no firewall on the
  # instance until the apply finishes), so it is intentionally terse and
  # matches exactly what exists today rather than being rewritten later.
  description = "SentinelOps: SSH from deploy machine, HTTP/HTTPS from anywhere"
  vpc_id      = data.aws_vpc.default.id

  # No HTTP/3 (UDP 443) rule. Caddy falls back to HTTP/2 without it — a small
  # loss, and adding it later is one more ingress rule, not a migration.
  ingress {
    description = "SSH, restricted to one address"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_allowed_cidr]
  }

  ingress {
    # AWS restricts SG rule descriptions to a narrow character set (no em
    # dash) — noted here since every other comment in this codebase uses one.
    description = "HTTP, Caddy redirects to HTTPS and needs it for the ACME challenge"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Unrestricted outbound: pulling images, cloning repositories, the ACME challenge, Trivy/Semgrep warm-up"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "sentinelops-web"
  }
}
