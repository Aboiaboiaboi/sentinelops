"""Serving a report from storage instead of rendering it again.

Rendering is the most expensive thing the API does — roughly 100ms of CPU for a
typical scan, against a single indexed row read for everything else. Most
reports are downloaded more than once and change almost never, which is the
shape a cache is for.

**What invalidates a copy, and why it is not a timestamp.** The key embeds a
fingerprint of the assembled report, so the stored key answers both questions
at once: whether a copy exists, and whether it is still the right one. Renaming
a scan changes the document, changes the fingerprint, changes the key, and the
next request re-renders. A report downloaded on Tuesday and one downloaded on
Friday will not disagree about the score, and neither will disagree with the
app — which is the property decision 3 of the phase plan asked for.

**Why the fingerprint covers the whole assembled report** rather than a handful
of columns. A short list of inputs is cheaper, and it is a list somebody has to
remember to extend: add a field to the document and the cache serves a stale
copy of it, silently and indefinitely. Hashing what the document actually says
cannot fall out of step with what the document says. It costs one findings query
on a cache hit, which is a fraction of the render it avoids.

**Storage failures do not fail the request.** This is the one place that treats
`StorageUnavailable` as recoverable, and the distinction matters against
milestone 1's argument that a silently unsaved report is worse than an error.
What is being written here is a *cache entry*, not the user's data. Nothing is
lost when it is not written — the next request renders again and the reader
gets the same bytes. Failing the download would turn an optimisation into a
hard dependency and take a working endpoint offline over a misconfigured
bucket. It is logged at warning level so the misconfiguration is loud.
"""

import asyncio
import hashlib
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import scan_service
from app.services.report_renderer import get_report_renderer
from app.services.report_service import ReportData
from app.utils.storage import StorageUnavailable, get_storage

logger = logging.getLogger(__name__)

# Bumped when a change to the renderer would make an already-stored PDF look
# different from a freshly rendered one. The fingerprint covers the report's
# *content*; this covers its presentation, which no amount of hashing the data
# can see. Forgetting to bump it means old copies keep the old layout until
# something else invalidates them — a stale layout, not stale facts.
RENDER_VERSION = "1"

#: Enough of the digest to make a collision irrelevant while keeping the key
#: readable in a bucket listing. 16 hex characters is 64 bits, against a
#: population of a few documents per scan.
_FINGERPRINT_LENGTH = 16


def fingerprint(report: ReportData) -> str:
    """A stable digest of everything the document states.

    `repr` of the dataclass tree, which reaches every field of every nested
    finding, check and category — and changes when any of them does. That
    includes fields added later, which is the whole point: the alternative is a
    hand-maintained list that silently stops covering the document.

    Deterministic across processes because every value in the tree is a string,
    number, UUID or datetime, none of which repr by identity.
    """
    material = f"{RENDER_VERSION}\n{report!r}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:_FINGERPRINT_LENGTH]


def storage_key(scan_id: uuid.UUID, digest: str) -> str:
    """Where a rendered report lives.

    Both components come from this process, not from user input — a UUID and a
    hex digest — so nothing here can escape the storage root. `LocalStorage`
    would refuse it anyway; the key is built safe rather than relying on that.
    """
    return f"scans/{scan_id}/report-{digest}.pdf"


async def render_or_reuse(
    db: AsyncSession, *, scan_id: uuid.UUID, stored_key: str | None, report: ReportData
) -> bytes:
    """The report's bytes, from storage when a copy of this exact document exists.

    **Storage is asked, not the column.** The key identifies the document, so
    whether a copy exists is a question only storage can answer — `report_key`
    records the most recent one, which is not the same thing. Gating the lookup
    on the column re-rendered a document that was still sitting in the bucket:
    rename a scan, download, rename it back, and the original copy was ignored
    because the column had moved on. The lookup costs one round trip on a miss,
    against the ~100ms render that miss is about to pay for.

    `stored_key` therefore decides only whether the row needs updating.
    """
    digest = fingerprint(report)
    key = storage_key(scan_id, digest)

    cached = await _read(key)
    if cached is not None:
        logger.debug("serving a cached report", extra={"scan_id": str(scan_id)})
        return cached

    # Off the event loop: CPU-bound work in an async handler stalls every other
    # request on this worker, including the three-second status polls.
    pdf = await asyncio.to_thread(get_report_renderer().render, report)

    if await _write(key, pdf) and stored_key != key:
        await scan_service.record_report_key(db, scan_id=scan_id, key=key)

    return pdf


async def _read(key: str) -> bytes | None:
    try:
        return await get_storage().download(key)
    except (StorageUnavailable, OSError) as exc:
        logger.warning(
            "could not read a cached report; rendering instead",
            extra={"key": key, "error": type(exc).__name__},
        )
        return None


async def _write(key: str, pdf: bytes) -> bool:
    """Store the rendered report. False if it could not be stored.

    The return value gates the database write: recording a key for an object
    that is not there would make every later request take the miss path
    *through* storage, which is slower than never having cached at all.
    """
    try:
        await get_storage().upload(key, pdf, content_type="application/pdf")
    except (StorageUnavailable, OSError) as exc:
        logger.warning(
            "could not store a rendered report; it will be rendered again next time",
            extra={"key": key, "error": type(exc).__name__},
        )
        return False
    return True
