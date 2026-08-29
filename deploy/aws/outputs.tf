output "instance_id" {
  value = aws_instance.app.id
}

output "public_ip" {
  description = "The instance's public IP. The app's live URL is <ip-with-dashes>.sslip.io — see deploy/aws/README.md."
  value       = aws_instance.app.public_ip
}

output "storage_bucket" {
  value = aws_s3_bucket.reports.bucket
}

output "ssh_command" {
  value = "ssh -i ~/.ssh/sentinelops-deploy.pem ubuntu@${aws_instance.app.public_ip}"
}
