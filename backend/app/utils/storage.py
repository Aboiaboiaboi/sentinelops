"""Object storage abstraction.

The second of the two modules permitted to know about a cloud SDK. Generated
PDF reports go through here; nothing else in the application should ever hold a
bucket client.

The local implementation writes under a configured directory, which is what
development uses. Cloud Storage or S3 becomes another class implementing the
same Protocol, and no caller changes.
"""

import asyncio
import os.path
from pathlib import Path
from typing import Protocol, runtime_checkable


class UnsafeStorageKey(ValueError):
    """Raised for a key that would write outside the storage root."""


@runtime_checkable
class Storage(Protocol):
    async def upload(self, key: str, content: bytes, *, content_type: str) -> str:
        """Store bytes under `key`, returning a locator for retrieving them.

        The locator is a path in development and a URL in a deployed
        environment, so callers should treat it as opaque and store it as given.
        """
        ...


class LocalStorage:
    """Filesystem-backed storage rooted at a single directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _resolve(self, key: str) -> Path:
        """Map a key to a path, refusing anything that escapes the root.

        Keys will eventually be built from scan ids, but this is the boundary
        where a path is derived from data, and it costs one comparison to make
        `../../etc/passwd` impossible rather than merely unlikely. Absolute keys
        are rejected for the same reason — Path joining silently discards the
        root when the second operand is absolute.
        """
        candidate = Path(key)
        if candidate.is_absolute() or candidate.drive:
            raise UnsafeStorageKey(f"Storage key must be a relative path: {key!r}")
        # Windows device names such as NUL or COM1 resolve inside the root but
        # do not behave like files. os.path.isreserved returns False everywhere
        # else, so this costs nothing on Linux.
        if os.path.isreserved(key):
            raise UnsafeStorageKey(f"Storage key is a reserved device name: {key!r}")

        destination = (self._root / candidate).resolve()
        if destination != self._root and self._root not in destination.parents:
            raise UnsafeStorageKey(f"Storage key escapes the storage root: {key!r}")
        return destination

    async def upload(self, key: str, content: bytes, *, content_type: str) -> str:
        # content_type is part of the interface because object stores need it as
        # response metadata. A filesystem has nowhere to put it, so it is
        # accepted and ignored rather than dropped from the signature.
        del content_type
        destination = self._resolve(key)

        def _write() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

        # Off the event loop: a report is small today, but a blocking write in
        # an async handler stalls every other request on the same worker.
        await asyncio.to_thread(_write)
        return str(destination)
