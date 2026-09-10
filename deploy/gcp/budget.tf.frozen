# A budget alert, created before anything expensive is.
#
# It cannot stop spending — no GCP budget can, and anyone who tells you
# otherwise has not read the documentation. What it does is email at thresholds,
# which is the difference between noticing a runaway on the second day and
# noticing it on the statement. A scan queue retrying a failing Cloud Run Job
# forever is billable, and that is not a hypothetical failure mode for this
# system.
#
# Skipped when billing_account is empty, because creating one needs permission
# on the billing account itself, which is a level above the project and may not
# be available to whoever runs this.

resource "google_billing_budget" "monthly" {
  count = var.billing_account == "" ? 0 : 1

  billing_account = var.billing_account
  display_name    = "SentinelOps"

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget)
    }
  }

  # 50% is the early warning, 90% is act now, and 100% of *forecasted* spend
  # fires when the current burn rate would exceed the budget by month end —
  # which is the one that catches a runaway on day two rather than day twenty.
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }
}

data "google_project" "current" {
  project_id = var.project_id
}
