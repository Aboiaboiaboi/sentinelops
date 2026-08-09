"""Object storage abstraction.

The second of the two modules permitted to know about a cloud SDK. Generated
PDF reports go through here; nothing else in the application should ever hold a
bucket client.

The local implementation writes under a configured directory, which is what
development uses. Cloud Storage or S3 becomes another class implementing the
same Protocol, and no caller changes.

Two implementations: `LocalStorage` for real use, and `NullStorage`, which is
the default and refuses. The reason for refusing differs from the sandbox's. A
missing sandbox must not produce a *passing* check; a missing storage backend
must not produce a *silently unsaved* report. A caller that believes it
persisted something and did not is worse off than one that got an error.
"""

import asyncio
import os.path
from pathlib import Path
from typing import Protocol, runtime_checkable


class UnsafeStorageKey(ValueError):
    """Raised for a key that would write outside the storage root."""


class StorageUnavailable(Exception):
    """No storage backend is configured here.

    Raised rather than returned so it cannot be mistaken for a successful
    write. A caller catching this must not report the artefact as saved.
    """


def _is_reserved_name(key: str) -> bool:
    """Whether the key names a Windows device (NUL, COM1, ...).

    os.path.isreserved is Windows-only — posixpath does not define it at all, so
    calling it unguarded raises AttributeError on Linux. That is invisible when
    developing on Windows and fails in the container, which is the only place it
    matters, so the lookup is done once here rather than at each call site.
    """
    isreserved = getattr(os.path, "isreserved", None)
    return isreserved is not None and isreserved(key)


@runtime_checkable
class Storage(Protocol):
    async def upload(self, key: str, content: bytes, *, content_type: str) -> str:
        """Store bytes under `key`, returning a locator for retrieving them.

        The locator is a path in development and a URL in a deployed
        environment, so callers should treat it as opaque and store it as given.
        """
        ...

    async def download(self, key: str) -> bytes | None:
        """Return what is stored under `key`, or None if nothing is.

        By key rather than by the locator `upload` returned, so that a caller
        holding only a key can read — and so a locator stays opaque. None for a
        miss rather than an exception, because the caller this exists for is a
        cache read, where "not stored" is an ordinary answer and not a failure.
        A backend that cannot be reached at all still raises.
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
        # do not behave like files. Meaningless on Linux, where the helper
        # reports False.
        if _is_reserved_name(key):
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

    async def download(self, key: str) -> bytes | None:
        # Resolved through the same guard as a write. A read is the less
        # alarming direction, but a key that escapes the root would still serve
        # an arbitrary file on disk to whoever asked for it.
        source = self._resolve(key)

        def _read() -> bytes | None:
            # Asked rather than caught: a key that another key nested under
            # names a directory, which holds no content and is therefore a
            # miss — but reading one raises IsADirectoryError on Linux and
            # PermissionError on Windows, and PermissionError on a real file is
            # a genuine failure that must not be reported as a miss.
            if source.is_dir():
                return None
            try:
                return source.read_bytes()
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_read)


class NullStorage:
    """Refuses every operation.

    The default, so that any environment where storage was never installed
    fails loudly at the first attempt to persist something instead of
    discarding it. Tests get this too — httpx's ASGITransport does not run
    lifespan events, so a test that needs storage installs it deliberately.
    """

    async def upload(self, key: str, content: bytes, *, content_type: str) -> str:
        del content, content_type
        raise StorageUnavailable(
            f"No storage backend is configured, so {key!r} was not saved. "
            "Set STORAGE_DIR and install a backend at startup."
        )

    async def download(self, key: str) -> bytes | None:
        # Not None. A miss and an absent backend are different facts, and
        # answering "nothing is stored there" would have a cache read report a
        # miss forever and re-render on every single request.
        raise StorageUnavailable(f"No storage backend is configured, so {key!r} could not be read.")


_storage: Storage = NullStorage()


def get_storage() -> Storage:
    return _storage


def set_storage(storage: Storage) -> None:
    """Swap the implementation. Called at startup once a storage directory or
    bucket is configured, and by tests that need somewhere real to write."""
    global _storage
    _storage = storage
