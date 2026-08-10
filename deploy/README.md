# Infrastructure

Everything SentinelOps runs on, as code. Terraform for all of it except the
handful of things Terraform cannot create for itself, which is what
`bootstrap.sh` is for.

Nothing here has been applied yet. It validates, and validating is not the same
as working — the first `terraform plan` against a real project is where the
remaining errors live.

## Running it

```bash
./bootstrap.sh my-project-id 01ABCD-234567-89EFGH   # once, ever
terraform init -backend-config=backend.hcl
terraform plan                                       # read this
```

**Read the plan.** It creates a Cloud SQL instance and a Memorystore instance,
and both bill from the moment they exist whether or not anything connects.

### The first apply, in three stages

Every apply after the first one is a plain `terraform apply`. The first one is
worth splitting up.

A single apply creates all forty-odd resources at once, ten in parallel. On a
project that has never had anything in it, several things can genuinely fail —
an API that has not finished enabling, a quota that has not been granted, a
field in here that is wrong. When they fail together the output is a wall, and
most of it is consequences of one real cause rather than separate problems.

```bash
# 1. The network. Slowest to fail and the most likely to, because it depends on
#    compute and servicenetworking being fully enabled rather than merely
#    requested — enabling an API returns before it is usable.
terraform apply -target=google_service_networking_connection.private_services

# 2. The two managed services that attach to it. Cloud SQL takes around ten
#    minutes. If the peering above is wrong, it surfaces here, on its own,
#    rather than tangled up with a Cloud Run error about a revision that never
#    started.
terraform apply \
  -target=google_sql_database_instance.main \
  -target=google_redis_instance.main

# 3. Everything else — service accounts, secrets, Cloud Run, WIF, the budget.
terraform apply
```

`-target` means "this resource and whatever it depends on, nothing else", which
is why stage 1 names only the peering: the VPC, the subnet and the reserved
range come along because it needs them.

**This is a first-apply aid, not a habit.** Routine `-target` use normally means
a configuration is badly structured or that drift is being worked around, and
HashiCorp documents it as an exceptional-recovery tool. Here it is doing one
thing: turning a single incomprehensible failure into three legible ones. Once
stage 3 succeeds, forget it exists.

Stages 1 and 2 also print `Note: Objects have changed outside of Terraform` or a
warning that the plan is partial. That is expected — a targeted apply knows it
did not consider everything, and it is telling you so.

## What it costs, and the teardown that goes with it

| Resource | | ~Monthly |
|---|---|---|
| Memorystore | Basic, 1 GB | **$35** |
| Cloud SQL | `db-f1-micro`, 10 GB | **$9** |
| Cloud Run service, worker pool, jobs | `min instances = 0` | ~$0 idle |
| Artifact Registry, Cloud Storage, logging | | ~$0 at this size |
| **Idle total** | | **~$45** |

Two of those bill continuously and nothing about scaling to zero helps. So:

```bash
terraform destroy
```

is part of the working routine, not an emergency measure. The configuration is
written to survive it — apply, destroy, apply again from clean state — because
infrastructure that only stands up once is a snapshot rather than code.
`deletion_protection` on the Cloud SQL instance guards the accident, not the
decision: `destroy` still removes it, after asking.

The budget alert is created *before* the expensive things, and it only alerts.
No GCP budget can stop spending.

## The layout

| File | |
|---|---|
| `bootstrap.sh` | Project, billing, APIs, state bucket. The chicken-and-egg set |
| `versions.tf` | Provider pins and the remote state backend |
| `variables.tf` | Every input, with why the default is the default |
| `network.tf` | VPC, subnet, and the peering Google-managed services attach to |
| `data_stores.tf` | Cloud SQL, Memorystore, the reports bucket, Artifact Registry |
| `iam.tf` | Three service accounts and exactly what each may do |
| `secrets.tf` | Secret Manager, and the one secret Terraform deliberately does not hold |
| `run.tf` | The API service, the worker pool, the migration job |
| `cicd.tf` | Workload Identity Federation, so GitHub Actions needs no key |
| `budget.tf` | The alert |
| `outputs.tf` | What the deploy workflow and the next milestone need |

## Four decisions worth knowing before changing anything

**No public IP on the database or on Redis.** Both are reachable only from
inside the VPC, and Cloud Run reaches them through direct VPC egress. The
payoff is in the application rather than here: `DATABASE_URL` and `REDIS_URL`
stay ordinary connection strings, with no proxy sidecar and no connector library
that knows this is Google.

**Direct VPC egress, not a Serverless VPC Access connector.** The connector
bills for two always-on instances whether or not anything is talking to the
database. Direct egress allocates an address per running instance and costs
nothing at zero. `PRIVATE_RANGES_ONLY`, so internet-bound traffic takes the
normal path and this does not also need a Cloud NAT.

**Three service accounts, not one.** Cloud Run defaults to the Compute Engine
default account, which holds project Editor — an unconfigured deployment gives
the API permission to delete the database. Each account here holds only what its
process calls, so a compromise of one is bounded by what that process could
already do.

**No service-account key exists.** GitHub Actions authenticates by Workload
Identity Federation, and the trust policy is scoped to one repository by an
attribute condition. A JSON key in a repository secret would be a credential
that never expires and works from anywhere.

## Known gaps

- **Cloud Run cannot pull from GHCR.** Not an authentication problem — the
  registry is not accepted. Hence the Artifact Registry repository here; the
  deploy workflow mirrors into it, and GHCR stays the public copy.
- **The sandbox is not defined yet.** `SANDBOX_ENABLED=false` on the worker, so
  the three tool-backed security checks report `errored` — honestly and
  visibly — until the Cloud Run Jobs implementation lands. That work also needs
  the Gitleaks, Trivy and Semgrep images mirrored into Artifact Registry, for
  the same reason as above.
- **`FORWARDED_ALLOW_IPS` is set from documentation, not measurement.** If it is
  wrong, every user shares one rate-limit bucket. Verified from two addresses in
  the production-configuration milestone rather than assumed here.
- **No Firebase Hosting.** The SPA and its `/api` rewrite are a separate
  milestone; `frontend_url` stays empty until then.
