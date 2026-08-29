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
  # steps. Ubuntu 24.04 LTS, amd64, us-east-1, resolved once via:
  #   aws ssm get-parameter --name \
  #     /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
  # Bump deliberately, the same way deploy/terraform.tfvars documents mirroring
  # a new image digest from GHCR.
  ami           = "ami-0d7f022123f8ff19d"
  instance_type = var.instance_type

  subnet_id                  = data.aws_subnet.app.id
  vpc_security_group_ids     = [aws_security_group.web.id]
  key_name                   = aws_key_pair.deploy.key_name
  iam_instance_profile       = aws_iam_instance_profile.ec2.name
  associate_public_ip_address = true

  root_block_device {
    volume_size = var.root_volume_size_gb
    volume_type = "gp3"
  }

  tags = {
    Name = "sentinelops"
  }
}
