"""Which scanner runs for which category.

A plain mapping rather than import-time auto-discovery. Auto-discovery reads
well until a scanner fails to import and simply stops existing — the scan then
reports that category as unreported and nothing says why. An explicit dict fails
loudly instead.

Categories with no entry are not run. They are recorded as failed and cost their
full weight, which is honest: nothing assessed them.
"""

from app.scanners.architecture import ArchitectureScanner
from app.scanners.base import Scanner
from app.scanners.deployment import DeploymentScanner

# Populated as each scanner lands. The pipeline handles a category with no entry
# by recording it as unreported, which costs its full weight — honest, since
# nothing assessed it.
SCANNERS: dict[str, Scanner] = {
    ArchitectureScanner.category: ArchitectureScanner(),
    DeploymentScanner.category: DeploymentScanner(),
}


def get_scanner(category: str) -> Scanner | None:
    return SCANNERS.get(category)
