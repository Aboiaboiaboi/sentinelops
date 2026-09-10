# Infrastructure

Three directories, three different jobs. Read the one that matches what you're
actually deploying.

| Directory | What it is | State |
|---|---|---|
| [`compose/`](compose/) | The application layer — the Docker Compose stack (app, Postgres, Redis, Caddy, and the Prometheus/Grafana/Loki monitoring stack) that runs on any Linux VM and names no cloud. | **Live.** This is what actually runs in production. |
| [`aws/`](aws/) | Terraform for the infrastructure *underneath* `compose/` on AWS: one EC2 instance, one S3 bucket, an IAM role, a security group. Six resources — proportionate to what a single VM needs. | **Live.** Provisions the box the compose stack runs on, in `ap-south-1`. |
| [`gcp/`](gcp/) | Terraform for a full managed-services deployment on GCP — dedicated VPC, Cloud SQL, Memorystore, Cloud Run, Workload Identity Federation. Forty-odd resources. | **Frozen.** Applied for real once, then the billing account lapsed. Kept as the "one cloud, done properly" reference. Its `.tf` files are renamed `.tf.frozen` so Terraform, Checkov and this project's own scanner all skip them. See [`gcp/README.md`](gcp/README.md). |

The short version: `aws/` + `compose/` is the deployment. `gcp/` is a
reference implementation that is no longer wired to anything.
