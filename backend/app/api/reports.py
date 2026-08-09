"""The PDF report endpoint.

Its own module rather than another route in scans.py: this is the only route in
the API that returns a binary body, the only one that is rate limited outside
auth, and the only one that reaches the renderer. Those are three reasons for a
reader to look in one place.

**Authentication rides on the cookie, and it has to.** The frontend opens a
plain `<a href target="_blank">` rather than fetching, because a tab navigation
is what produces a file the browser saves. There is no way to attach an
`Authorization` header to a navigation, so the httpOnly cookie already in place
is what makes this route reachable at all.
"""

import re
import urllib.parse
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import CurrentUser, DbSession
from app.config import get_settings
from app.models import ScanStatus
from app.rate_limit import limiter
from app.services import report_cache, scan_service
from app.services.report_service import ReportData, build_report

router = APIRouter(tags=["reports"])

#: Scans are terminal in exactly these two states, matching isScanFinished in
#: the frontend. A failed scan still gets a report: it carries the failure and
#: the hint, which is the document somebody actually wants in that case.
FINISHED = (ScanStatus.COMPLETED, ScanStatus.FAILED)

#: Everything a filename may contain after sanitising. Deliberately narrow —
#: this string ends up in a response header and then on a filesystem, and the
#: characters that matter there (quotes, semicolons, CR, LF, slashes, NUL) are
#: all outside it.
_FILENAME_SAFE = re.compile(r"[^a-zA-Z0-9]+")
_FILENAME_STEM_LIMIT = 60


def download_filename(report: ReportData) -> str:
    """A filename for the download, built from data the user controls.

    Scans and projects are user-named, and this value crosses two boundaries
    that both punish unsanitised text: an HTTP header, where a CR or LF would
    let a name inject one, and a filesystem, where a slash or a device name
    would decide where the file lands. Reducing to `[a-z0-9-]` makes every one
    of those impossible rather than individually handled — the same argument as
    LocalStorage._resolve rejecting keys outright.
    """
    label = report.scan_name or report.project_name
    stem = _FILENAME_SAFE.sub("-", label).strip("-").lower()[:_FILENAME_STEM_LIMIT].strip("-")
    # A name of nothing but punctuation, or a non-Latin name, reduces to empty.
    # The date and the fixed prefix still identify the file.
    stem = f"-{stem}" if stem else ""
    return f"sentinelops{stem}-{report.created_at:%Y-%m-%d}.pdf"


def _content_disposition(filename: str) -> str:
    """`attachment` with both the plain and the encoded form.

    `filename*` is what a browser prefers, and `filename` is the fallback for
    anything that does not understand RFC 5987. Both are produced from the
    already-sanitised ASCII stem, so the two cannot disagree.
    """
    quoted = urllib.parse.quote(filename)
    return f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quoted}"


@router.get(
    "/scans/{scan_id}/report",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The rendered report."},
        404: {"description": "No such scan, or it is not yours."},
        409: {"description": "The scan has not finished."},
    },
)
# The most expensive GET in the API: it loads a scan, its findings and its
# project, then renders a document. Reachable by opening a tab in a loop.
@limiter.limit(get_settings().report_rate_limit)
async def get_report(
    scan_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    db: DbSession,
) -> Response:
    """The scan as a PDF.

    `request` is unused here and required: slowapi reads the client address off
    it, and the decorator raises at startup without it in the signature.
    """
    del request

    inputs = await scan_service.get_report_inputs(db, owner=user, scan_id=scan_id)
    if inputs is None:
        # The same answer for "no such scan" and "not yours", as everywhere
        # else in this API. Distinguishing them turns the endpoint into an
        # oracle for which scan ids exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found.")

    if inputs.scan.status not in FINISHED:
        # 409 rather than 404. The scan exists and this is its report; it does
        # not exist *yet*. A 404 here would say something untrue, and would be
        # indistinguishable from the answer given to somebody else's scan.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This scan has not finished yet. The report is available once it completes.",
        )

    report = build_report(inputs.scan, project=inputs.project, findings=inputs.findings)
    pdf = await report_cache.render_or_reuse(
        db,
        scan_id=inputs.scan.id,
        stored_key=inputs.scan.report_key,
        report=report,
    )

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(download_filename(report)),
            # A report lists this repository's security findings. no-store keeps
            # it out of shared caches and off disk in the browser's cache, which
            # matters more here than the round trip it costs — the document is
            # small and the endpoint is rate limited anyway.
            "Cache-Control": "no-store",
        },
    )
