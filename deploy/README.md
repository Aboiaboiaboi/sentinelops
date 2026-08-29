# Infrastructure

Everything SentinelOps runs on, as code, for the one-cloud managed-services
deployment. `deploy/compose/` next to this directory is the other path — a
Docker Compose stack that runs on any VM on any cloud and names none of them;
read its README instead if that is what you are looking for. This directory is
Terraform, for all of it except the handful of things Terraform cannot create
for itself, which is what `bootstrap.sh` is for.

## Current state — read this before touching anything here

**This was applied for real, once, on 2026-08-10 — the "Nothing here has been
applied yet" this file used to open with was wrong the moment that happened.**
The three-stage apply below ran against `sentinelops-fyp-2026`: the network
came up clean, Cloud SQL and Memorystore provisioned, and stage three finished
— service accounts, secrets, Cloud Run, WIF, the budget alert, all of it. The
migration job ran and exited 0. The API reached `Ready` and served real
traffic.

**Then the project's billing account lapsed**, and everything here has been
frozen since. `gcloud billing projects describe sentinelops-fyp-2026` reads
`billingEnabled: false`; Cloud SQL shows `SUSPENDED`; Memorystore refuses API
calls outright (`PERMISSION_DENIED: ... requires billing to be enabled`); the
deployed API answers `HTTP 500`. `terraform destroy` cannot run in this state
either — deleting billed resources needs billing enabled — so the project can
currently be neither used nor cleaned up.

**GCP deletes a project roughly 30 days after billing is disabled.** The lapse
was on or shortly after 2026-08-10, so that window was closing as of this
writing and may already have passed. When it does, the Terraform state file in
this project's GCS bucket goes with it, and this configuration stops
describing anything real. That is an acceptable outcome, not an emergency —
nothing here is the working deployment any more (`deploy/compose/` is) — but it
is worth knowing rather than discovering.

**Next deployment is planned for AWS or Azure**, not a return to this project.
This Terraform stays as the "one cloud, done properly" reference — real
managed services, least-privilege IAM, Workload Identity Federation — and as
documentation of what a from-scratch cloud deployment looked like the one time
it was actually run. See `11-phase5-handoff.md` (outside the repository, in the
handoff documents) for the full account.

## Running it, if you do decide to revive or fork this

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

- **The sandbox was never going to be defined here.** `SANDBOX_ENABLED=false`
  on the worker, so the three tool-backed security checks would report
  `errored` — honestly and visibly — on any deployment built from this
  Terraform, indefinitely. The Cloud Run Jobs implementation this once waited
  on (`CloudRunJobSandbox`, Phase 5 milestone 3) is not going to land: it was a
  GCP-shaped class with an ECS- or Container-Instances-shaped equivalent on
  every other cloud, and `deploy/compose/` exists specifically because
  `DockerSandbox` already runs anywhere — see `11-phase5-handoff.md`. If this
  Terraform is ever revived, the sandbox is the reason not to expect a
  headline security score from it without also solving this.
- **`FORWARDED_ALLOW_IPS` is very likely the wrong variable name.** It is set
  here on `run.tf`'s API service, but Uvicorn's CLI reads its own flags from
  environment variables with a `UVICORN_` prefix (`click`'s
  `auto_envvar_prefix`) — the flag is `--forwarded-allow-ips`, so the variable
  Uvicorn actually reads is `UVICORN_FORWARDED_ALLOW_IPS`, not
  `FORWARDED_ALLOW_IPS`. Found while building and verifying `deploy/compose/`,
  where getting this right was necessary for login to work at all. Never
  measured against the real deployment before it froze, so it is recorded here
  as a strong suspicion rather than a confirmed bug — but if true, every user
  behind Cloud Run's front end shared one rate-limit bucket the entire time
  this ran. Worth the five minutes to check before ever reusing this file.
- **Cloud Run cannot pull from GHCR.** Not an authentication problem — the
  registry is not accepted. Hence the Artifact Registry repository here; the
  deploy workflow mirrors into it, and GHCR stays the public copy.
- **No Firebase Hosting.** The SPA and its `/api` rewrite are a separate
  milestone; `frontend_url` stays empty until then.
