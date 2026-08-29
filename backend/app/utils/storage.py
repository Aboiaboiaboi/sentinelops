"""Object storage abstraction.

The second of the two modules permitted to know about a cloud SDK. Generated
PDF reports go through here; nothing else in the application should ever hold a
bucket client.

Four implementations: `LocalStorage` writes under a configured directory and is
what development uses, `GcsStorage` talks to a Cloud Storage bucket, `S3Storage`
talks to anything that speaks the S3 API — real AWS, or an S3-compatible
endpoint such as Cloudflare R2, DigitalOcean Spaces or MinIO — and `NullStorage`
is the default and refuses. The reason for refusing differs from the sandbox's.
A missing sandbox must not produce a *passing* check; a missing storage backend
must not produce a *silently unsaved* report. A caller that believes it
persisted something and did not is worse off than one that got an error.

**Neither cloud SDK is a dependency of this module.** `google-cloud-storage` and
`boto3` live in the optional `gcs` and `s3` dependency groups and are imported
inside their respective classes' `__init__`, never at module scope. An install
without either imports this file, runs the suite, and uses LocalStorage exactly
as before — which is what keeps "zero cloud SDKs" true of the default install,
and with it the argument that a new deployment target is a new class here
rather than a redesign.
"""

import asyncio
import logging
import os.path
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def _is_rooted(key: str) -> bool:
    """Whether a key begins at a root, under the stricter of the two platforms.

    Deliberately not `Path(key).is_absolute()`, which is the obvious version and
    is wrong twice over. It answers True for `/x` on Linux and **False on
    Windows**, where absoluteness requires a drive — so the guard would pass
    locally and reject in the container, or the reverse. And `is_absolute()` is
    False for `\\x` even on Windows, though `root / "\\x"` there discards the
    root and keeps only the drive, which is exactly the escape being guarded
    against.

    `PureWindowsPath` is asked because its rules are the superset: it treats
    both separators as separators, and it is the only one of the two with a
    concept of a drive. Both were found by running the guard and reading what it
    said, not by reading the documentation.
    """
    candidate = PureWindowsPath(key)
    return bool(candidate.root) or bool(candidate.drive)


class UnsafeStorageKey(ValueError):
    """Raised for a key that would write outside the storage root."""


class StorageUnavailable(Exception):
    """No storage backend is configured here.

    Raised rather than returned so it cannot be mistaken for a successful
    write. A caller catching this must not report the artefact as saved.
    """


def _check_flat_key(key: str) -> None:
    """Guard shared by every bucket-backed store — GCS and S3 alike.

    A bucket is flat and `../etc/passwd` is a perfectly legal object name, so
    none of `LocalStorage._resolve`'s containment reasoning transfers — there is
    no root to escape. The guard is kept anyway, and deliberately: the same key
    is what `LocalStorage` writes to disk in development, and it reaches a
    `Content-Disposition` header on the way out. A key that is safe in one
    backend and not another is a bug that only appears in production.

    An empty key is rejected too. Both GCS and S3 accept one and create an
    object that cannot be addressed by name afterwards.
    """
    if not key or key != key.strip():
        raise UnsafeStorageKey(f"Storage key must be a non-blank, untrimmed path: {key!r}")
    if _is_rooted(key):
        raise UnsafeStorageKey(f"Storage key must be a relative path: {key!r}")
    if _is_reserved_name(key):
        raise UnsafeStorageKey(f"Storage key is a reserved device name: {key!r}")
    # Both separators, because this key is also handed to LocalStorage in
    # development, where a backslash is one.
    if ".." in PurePosixPath(key).parts or ".." in PureWindowsPath(key).parts:
        raise UnsafeStorageKey(f"Storage key escapes the storage root: {key!r}")


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


class GcsStorage:
    """Cloud Storage backed storage for a single bucket.

    Credentials are never passed in. The client resolves them from the
    environment, which on Cloud Run means the service account attached to the
    container — no key stored, nothing to rotate, and nothing about *where* this
    runs written down in the application. The bucket name is the whole of the
    configuration.
    """

    def __init__(self, bucket: str) -> None:
        # Imported here rather than at module scope. Everything else in this
        # file works without the SDK installed, and the worker image has no use
        # for it at all — a top-level import would make a cloud library a hard
        # requirement of importing the storage boundary.
        from google.cloud import storage  # noqa: PLC0415

        self._bucket_name = bucket
        # One client, reused. Constructing one per call would re-resolve
        # credentials and open a new connection pool for every report.
        self._bucket = storage.Client().bucket(bucket)

    def _blob(self, key: str):
        """Map a key to a blob, refusing anything a filesystem would refuse."""
        _check_flat_key(key)
        return self._bucket.blob(key)

    async def upload(self, key: str, content: bytes, *, content_type: str) -> str:
        blob = self._blob(key)

        def _write() -> None:
            blob.upload_from_string(content, content_type=content_type)

        # The SDK is synchronous — it is `requests` underneath. Called directly
        # from a handler it would block the event loop for the whole round trip
        # to Google, which is the failure mode bcrypt already demonstrated at
        # 1561ms. Same treatment LocalStorage.upload gives a disk write.
        await asyncio.to_thread(_write)
        # gs:// rather than an https URL: the locator is opaque to callers and
        # is stored, and a signed or public URL would expire or leak.
        return f"gs://{self._bucket_name}/{key}"

    async def download(self, key: str) -> bytes | None:
        from google.cloud.exceptions import NotFound  # noqa: PLC0415

        blob = self._blob(key)

        def _read() -> bytes | None:
            try:
                return blob.download_as_bytes()
            except NotFound:
                # A miss, and an ordinary one — this is a cache read. Every
                # other failure, including a permission error and an unreachable
                # bucket, propagates: answering "nothing is stored there"
                # because the credentials are wrong would re-render on every
                # single request and never say why.
                return None

        return await asyncio.to_thread(_read)


class S3Storage:
    """S3-API backed storage for a single bucket.

    Not "AWS support" so much as most-clouds support: S3's API is the de facto
    standard, so this one class also serves Cloudflare R2, DigitalOcean Spaces,
    MinIO and GCS's own interop endpoint — an `endpoint_url` is the entire
    difference between them. Credentials are never passed in, the same
    discipline as `GcsStorage`: boto3 resolves them from the environment, which
    on EC2 or ECS means the instance or task role attached to the process.
    """

    def __init__(self, bucket: str, *, endpoint_url: str = "", region: str = "") -> None:
        # Imported here rather than at module scope, for the same reason as
        # google-cloud-storage in GcsStorage above: everything else in this file
        # works without the SDK installed, and a top-level import would make a
        # second cloud library a hard requirement of importing the boundary.
        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            # Empty means real AWS, resolved by boto3's own endpoint logic. Set
            # for anything S3-compatible — R2, Spaces, MinIO, the GCS interop
            # endpoint — where the value is the provider's own URL.
            endpoint_url=endpoint_url or None,
            region_name=region or None,
            # Path-style addressing (bucket in the path, not the host) is what
            # every non-AWS S3-compatible endpoint expects, and what a local
            # MinIO cannot serve any other way — virtual-hosted style needs a
            # bucket name that is also a valid DNS subdomain of the endpoint.
            # Real AWS accepts path-style too, so this is safe unconditionally
            # rather than only when endpoint_url is set.
            config=Config(s3={"addressing_style": "path"}),
        )

    def _key(self, key: str) -> str:
        """Validate a key, refusing anything a filesystem would refuse."""
        _check_flat_key(key)
        return key

    async def upload(self, key: str, content: bytes, *, content_type: str) -> str:
        key = self._key(key)

        def _write() -> None:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=content, ContentType=content_type
            )

        # boto3 is synchronous. Off the event loop for the same reason as every
        # other backend here — a blocking round trip in an async handler stalls
        # every other request on the same worker.
        await asyncio.to_thread(_write)
        # s3:// rather than an https URL, matching GcsStorage's gs://: the
        # locator is opaque to callers and is stored, and a presigned or public
        # URL would expire or leak.
        return f"s3://{self._bucket}/{key}"

    async def download(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError  # noqa: PLC0415

        key = self._key(key)

        def _read() -> bytes | None:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
                return response["Body"].read()
            except ClientError as exc:
                # A miss, and an ordinary one — this is a cache read. AWS S3
                # answers NoSuchKey; MinIO and some S3-compatible providers
                # answer a bare 404 with no such code, so both are checked.
                # Every other failure, including a permission error and an
                # unreachable bucket, propagates: answering "nothing is stored
                # there" because the credentials are wrong would re-render on
                # every single request and never say why.
                error = exc.response.get("Error", {})
                if error.get("Code") in {"NoSuchKey", "404"}:
                    return None
                raise

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
