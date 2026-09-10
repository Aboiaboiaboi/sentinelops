# The private network, and the reason it exists at all.
#
# Cloud SQL and Memorystore are both reachable only on private addresses in this
# design. That is not belt-and-braces: a Postgres instance with a public IP is
# reachable from the internet and defended by a password, and Memorystore has no
# authentication worth the name. Keeping both off the public internet means the
# only way in is from something already inside this network.
#
# The payoff in the application is that DATABASE_URL and REDIS_URL stay ordinary
# connection strings. No proxy sidecar, no connector library, nothing in
# requirements.txt that knows this is Google — which is the portability position
# in section "How much of this is tied to one cloud" holding up under contact.

resource "google_compute_network" "main" {
  name = "sentinelops"
  # Subnets are declared below rather than one appearing automatically in every
  # region on earth. Auto mode creates twenty-odd subnets, which is twenty-odd
  # ranges to collide with anything peered later.
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "main" {
  name          = "sentinelops-${var.region}"
  region        = var.region
  network       = google_compute_network.main.id
  ip_cidr_range = "10.8.0.0/20"

  # Cloud Run's direct VPC egress allocates an address per instance out of this
  # range, so a /20 is sizing for scale rather than generosity: at /28 the
  # service stops scaling when the subnet fills, and the error reads like a
  # quota problem rather than an addressing one.

  # Flow logs off. They are billed per gigabyte and nothing here reads them.
  # Turn them on if a network question ever needs answering with evidence.
}

# Google-managed services — Cloud SQL and Memorystore among them — live in a
# network Google owns and reach this one over a peering. That peering needs a
# range on this side reserved for their addresses, and it must not overlap the
# subnet above.
resource "google_compute_global_address" "private_services" {
  name          = "sentinelops-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]

  # Without this, `terraform destroy` leaves the peering behind and the network
  # cannot be deleted — the failure looks like a dependency bug in Terraform and
  # is actually a real orphaned resource.
  deletion_policy = "ABANDON"
}
