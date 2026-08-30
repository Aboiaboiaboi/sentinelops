# The one VM. Everything above this file exists to give it network access,
# an identity, and somewhere to write reports; everything the VM itself runs
# — Docker, the containers, Caddy, the app — is deploy/compose/, not here.
# This directory's job stops at "a Linux box with a public IP and an IAM role
# attached," which is deliberately as small a surface as an EC2 deployment has.

resource "aws_key_pair" "deploy" {
  key_name   = "sentinelops-deploy"
  public_key = var.ssh_public_key

  # AWS's DescribeKeyPairs does not return public key material on a normal
  # refresh — only describe-key-pairs --include-public-key does, which the
  # provider's read does not use — so an imported key pair's state can never
  # actually hold public_key, and every plan shows a permanent diff wanting
  # to replace it. Ignored rather than chased: the fingerprint above already
  # proves the key in AWS matches var.ssh_public_key, and replacing this
  # resource would generate a new key pair *name* collision, not a new key.
  lifecycle {
    ignore_changes = [public_key]
  }
}

resource "aws_instance" "app" {
  # Pinned to a specific AMI rather than resolved from an "always latest"
  # SSM parameter, deliberately — the project's own deployment.image_pinning
  # check exists to catch exactly the floating-reference version of this
  # mistake, and a `terraform plan` that wants to replace the instance every
  # time Canonical publishes a new build would be that mistake with extra
  # steps. Ubuntu 24.04 LTS, amd64, ap-south-1 (AMI ids are region-specific
  # even for the identical image — this changed when the region did),
  # resolved once via:
  #   aws ssm get-parameter --region ap-south-1 --name \
  #     /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
  # Bump deliberately, the same way deploy/terraform.tfvars documents mirroring
  # a new image digest from GHCR.
  ami           = "ami-050c78efa486a0196"
  instance_type = var.instance_type

  subnet_id              = data.aws_subnet.app.id
  vpc_security_group_ids = [aws_security_group.web.id]
  key_name               = aws_key_pair.deploy.key_name
  iam_instance_profile   = aws_iam_instance_profile.ec2.name
  # No ephemeral auto-assigned address — aws_eip.app below is what actually
  # gives this instance a public IP now, and it is a fixed one rather than a
  # new address on every stop/start. Also sidesteps a real trap: while the
  # instance is stopped, AWS reports associate_public_ip_address as false
  # (a stopped instance has no active network interface to hold an ephemeral
  # address), which would otherwise show as a diff wanting to replace the
  # instance every time terraform plan runs against a stopped box.

  root_block_device {
    volume_size = var.root_volume_size_gb
    volume_type = "gp3"
  }

  tags = {
    Name = "sentinelops"
  }
}

# A fixed address, deliberately — the instance is stopped between sessions to
# avoid paying for compute while nobody is using it (deploy/aws/README.md),
# and an auto-assigned public IP changes on every stop/start. That silently
# broke three things at once the first time it happened: the sslip.io domain
# this deployment's TLS certificate is issued for, the DEPLOY_DOMAIN GitHub
# Actions variable, and any GitHub App callback URL pointing at the instance.
#
# Not free while stopped: AWS bills roughly $3.60/month for a reserved
# address attached to a stopped instance (public IPv4 has not been free since
# Feb 2024), on top of nothing while the instance is running, where it is
# free like any other attached EIP. That is the accepted cost of not having
# to reconfigure DNS, CI, and the GitHub App every time the instance restarts
# — see deploy/aws/README.md.
resource "aws_eip" "app" {
  instance = aws_instance.app.id
  domain   = "vpc"

  tags = {
    Name = "sentinelops"
  }
}
