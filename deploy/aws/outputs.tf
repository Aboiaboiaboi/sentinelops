output "instance_id" {
  value = aws_instance.app.id
}

output "public_ip" {
  description = <<-EOT
    The instance's fixed public IP (the Elastic IP, not the ephemeral address
    the instance itself reports) — this is the one that survives a
    stop/start. The app's live URL is <ip-with-dashes>.sslip.io, e.g.
    3-88-1-2.sslip.io for 3.88.1.2 — see deploy/aws/README.md.
  EOT
  value       = aws_eip.app.public_ip
}

output "sslip_domain" {
  description = "The public_ip above, pre-formatted as the sslip.io domain DOMAIN in deploy/compose/.env and DEPLOY_DOMAIN in GitHub Actions both want."
  value       = "${replace(aws_eip.app.public_ip, ".", "-")}.sslip.io"
}

output "storage_bucket" {
  value = aws_s3_bucket.reports.bucket
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/sentinelops-deploy.pem ubuntu@${aws_eip.app.public_ip}"
}
