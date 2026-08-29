# AWS

The infrastructure underneath `deploy/compose/` on AWS: one EC2 instance, one
S3 bucket, the IAM role that lets the instance write to it, and the security
group that decides what can reach it. Everything the instance actually *runs*
— Docker, the containers, Caddy, the app — is `deploy/compose/`, not here.
This directory's job stops at "a Linux box with a public IP and an IAM role
attached."

Where `../*.tf` is the one-cloud, managed-services deployment for GCP — a
dedicated VPC, Cloud SQL, Memorystore, Workload Identity Federation — this is
deliberately smaller. One VM running Docker Compose, in the account's existing
default VPC, with local Terraform state rather than a remote backend. Six
resources, not forty. See `versions.tf` and `network.tf` for why each of those
simplifications is fine at this size and what would make them not fine.

## What's running right now

**Currently stopped, deliberately** — the instance is stopped between working
sessions rather than left running (and billing) with nobody using it. First
deployed 2026-08-29 at commit `ccd24a2`, self-scan **94/100, Grade A**,
verified end to end: signup, a real scan, the sandbox, and a PDF report that
round-trips through the actual S3 bucket below, not a stand-in.

| | |
|---|---|
| Instance | `i-09ef906ff63661005`, t3.small, us-east-1a |
| Storage bucket | `sentinelops-reports-473183365846` |
| SSH | `ssh -i ~/.ssh/sentinelops-deploy.pem ubuntu@<address>` — see below for what `<address>` is |

**A fixed address (`aws_eip.app`, in `instance.tf`) is configured but not yet
applied.** Until `terraform apply` runs, the instance's public IP is
ephemeral and changes on every stop/start — which the first deployment
learned the hard way, since it silently breaks the sslip.io domain below,
`DEPLOY_DOMAIN` in GitHub Actions, and any GitHub App callback pointing at
the instance, all three at once. Applying is one command:

```bash
cd deploy/aws
terraform apply
```

**After that (or whenever the address changes for any other reason),** the
domain the app lives at is `terraform output -raw sslip_domain`, which
resolves through [sslip.io](https://sslip.io) — a free wildcard DNS service
with no signup: `<ip-with-dashes>.sslip.io` resolves to that literal IP, and
because it's a real, publicly resolvable domain, Caddy gets a genuine Let's
Encrypt certificate for it rather than a self-signed one. If a real domain is
ever pointed here instead, update `DOMAIN` in `deploy/compose/.env` on the
instance and restart the `frontend` container — Caddy requests a fresh
certificate for whatever it's given.

## Running it

```bash
cd deploy/aws
cp terraform.tfvars.example terraform.tfvars
# fill in ssh_allowed_cidr, ssh_public_key, storage_bucket_name — see the
# comments in terraform.tfvars.example for exactly what each needs
terraform init
terraform plan   # read this
terraform apply
```

Terraform's job ends at "a reachable VM with an IAM role." It does not deploy
the application — SSH in and follow `deploy/compose/README.md` from there:
clone the repository, fill in `deploy/compose/.env` (the bucket name from
`terraform output storage_bucket` and a domain), run `provision.sh`.

## Decisions worth knowing before changing anything

**Local Terraform state, not a remote backend.** `versions.tf` explains the
trade: an S3+DynamoDB backend is right once a second person applies changes to
this, and costs a bootstrap step to set up before that day. Until then,
`terraform.tfstate` living next to the config (gitignored, same as
`deploy/terraform.tfvars`) is simpler and loses nothing a solo deployment
needs.

**No dedicated VPC.** The instance runs in the account's default VPC and
subnet, read as data sources rather than created. `../network.tf` builds a
real VPC for GCP because that deployment also runs Cloud SQL and Memorystore
on private IPs behind it — there is a genuine peering and egress story there.
Here, Postgres and Redis are containers on the same host as the API; there is
nothing for a dedicated VPC to isolate that Docker's own network doesn't
already.

**SSH restricted to one IP, HTTP/HTTPS open to everyone.** The obvious shape —
this is a public web app, and an SSH port reachable from the whole internet is
a credential-stuffing target from the moment it exists. `ssh_allowed_cidr`
needs updating (`terraform apply` after) if the deploying machine's IP
changes.

**The AMI is pinned, not resolved from "always latest."** `instance.tf`
explains why at length: the project's own `deployment.image_pinning` check
exists to catch exactly the floating-reference version of this mistake, and a
`terraform plan` that wants to replace the instance every time Canonical ships
a new Ubuntu build would be that mistake with extra steps. Bump the AMI
deliberately.

**No AWS access key anywhere.** The instance's IAM role
(`sentinelops-ec2-role`) is how `S3Storage` authenticates — boto3 resolves
credentials from the instance metadata service automatically. `AWS_ACCESS_KEY_ID`
and `AWS_SECRET_ACCESS_KEY` in `deploy/compose/.env` stay empty on AWS for
exactly this reason; they exist in that file for R2, Spaces or MinIO, which
have no equivalent to an instance role.

**An Elastic IP, not the ephemeral address.** `aws_eip.app` in `instance.tf`
costs nothing while the instance is running — same as any address attached to
a running instance — but roughly **$3.60/month while it's stopped**, since
AWS stopped giving public IPv4 addresses away for free in February 2024. That
is a deliberate trade against the alternative: without it, every stop/start
(and the instance is stopped between sessions on purpose — see above) breaks
the sslip.io domain, `DEPLOY_DOMAIN` in Actions, and any GitHub App callback,
all three at once, silently, until someone notices the site is unreachable.

## A real bug this deployment found

The first IAM policy for `sentinelops-ec2-role` granted `s3:GetObject` and
`s3:PutObject` on the bucket's objects, and nothing else. The very first PDF
download failed with a 500: `AccessDenied` on `GetObject`, even though the
role could read objects that existed. The cause is a genuine S3 subtlety —
a `GetObject` for a key that does **not** exist yet (the cache-miss path every
report render starts with: check the cache, render if absent) needs
`s3:ListBucket` on the bucket itself. Without it, S3 cannot safely tell the
caller "this key does not exist" versus "you are not allowed to know whether
it exists," and answers `AccessDenied` for both. `iam.tf`'s policy grants
`ListBucket` on the bucket ARN as a second, deliberate statement — not
folded into the object-level statement, because it is a different resource
type (the bucket, not `bucket/*`) and IAM would reject combining them.

## Known gaps, honestly

- **Deploy-on-push exists, but authenticates with a stored SSH key, not
  OIDC.** `.github/workflows/deploy.yml` deploys on every push to `main` via
  `DEPLOY_SSH_KEY`, a long-lived secret in GitHub Actions — not the
  no-stored-credential pattern Phase 5 milestone 5 specified for GCP's
  Workload Identity Federation (see `11-phase5-handoff.md`). An AWS
  equivalent (GitHub's OIDC provider federated to an IAM role, no key at
  rest) is a real improvement over what's here, not yet built.
- **No CloudWatch, no alerting.** If the instance runs out of disk or the
  Docker daemon dies at 3am, nothing pages anyone. This is the same
  observability gap the main README's self-scan reports about the
  application itself (Observability −4) — the infrastructure has the matching
  gap, and closing both is one session, not two.
- **Single point of failure by design, not oversight.** One VM, one Postgres,
  one Redis, all on one host. That is the accepted trade this whole deployment
  path makes for "works on any cloud's cheapest VM" — see
  `deploy/compose/README.md` and `11-phase5-handoff.md` for where the managed,
  highly-available alternative lives (`../*.tf`, for GCP, when that account's
  billing is usable again).
