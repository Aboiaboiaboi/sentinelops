"""Tests for the Cloud Storage backend.

Split from test_utils.py because these need a server. They run against
**fake-gcs-server**, a real implementation of the GCS JSON API over HTTP, not
against a mock of the SDK — a mock would assert that this code calls the methods
this code calls, which is the shape of test that passes while the feature is
broken. The one bug found while writing this (a key guard that behaved
differently on Windows and Linux) was not the kind a mock can see.

Start the server with:

    docker run --rm -p 4443:4443 fsouza/fake-gcs-server:1.52 \
        -scheme http -port 4443 -public-host localhost:4443

Without it every test here skips, so the suite stays runnable with no Docker —
the same arrangement as the sandbox integration test.
"""

import os
import socket
import uuid

import pytest

from app.utils.storage import Storage, UnsafeStorageKey

# google-cloud-storage lives in the optional `gcs` dependency group, so an
# install without it must skip rather than error — that is the whole point of
# the group. `uv sync --all-groups` installs it, which is what CI does.
pytest.importorskip("google.cloud.storage", reason="the gcs dependency group is not installed")

EMULATOR_HOST = os.environ.get("STORAGE_EMULATOR_HOST", "http://localhost:4443")


def _emulator_is_up() -> bool:
    host, _, port = EMULATOR_HOST.removeprefix("http://").removeprefix("https://").partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _emulator_is_up(), reason=f"no GCS emulator listening on {EMULATOR_HOST}"
)


@pytest.fixture
def bucket_name() -> str:
    """A fresh bucket per test, so one test's objects cannot answer another's
    download."""
    from google.cloud import storage as gcs

    # Set before the client is built: the SDK reads it when resolving the base
    # URL and, seeing it, authenticates anonymously.
    os.environ["STORAGE_EMULATOR_HOST"] = EMULATOR_HOST
    name = f"sentinelops-test-{uuid.uuid4().hex[:12]}"
    gcs.Client(project="test").create_bucket(name)
    return name


@pytest.fixture
def storage(bucket_name: str):
    from app.utils.storage import GcsStorage

    return GcsStorage(bucket_name)


class TestRoundTrip:
    async def test_upload_then_download_returns_the_same_bytes(self, storage) -> None:
        await storage.upload("reports/a.pdf", b"%PDF-1.4 body", content_type="application/pdf")

        assert await storage.download("reports/a.pdf") == b"%PDF-1.4 body"

    async def test_upload_returns_a_gs_locator(self, storage, bucket_name: str) -> None:
        """Opaque to callers and stored as given, so it must not be a signed or
        public URL — those expire, and one of them would leak."""
        locator = await storage.upload("reports/a.pdf", b"x", content_type="application/pdf")

        assert locator == f"gs://{bucket_name}/reports/a.pdf"

    async def test_the_content_type_reaches_the_object(self, storage, bucket_name: str) -> None:
        """It is response metadata on the way back out. LocalStorage has nowhere
        to put it and drops it; here it has to survive."""
        from google.cloud import storage as gcs

        await storage.upload("reports/a.pdf", b"x", content_type="application/pdf")

        blob = gcs.Client(project="test").bucket(bucket_name).get_blob("reports/a.pdf")
        assert blob.content_type == "application/pdf"

    async def test_upload_overwrites(self, storage) -> None:
        await storage.upload("reports/a.pdf", b"first", content_type="application/pdf")
        await storage.upload("reports/a.pdf", b"second", content_type="application/pdf")

        assert await storage.download("reports/a.pdf") == b"second"

    async def test_a_missing_key_is_none_not_an_exception(self, storage) -> None:
        """The caller is a cache read, where "not stored" is an ordinary answer.
        Raising here would turn every cold report into an error."""
        assert await storage.download("reports/never-written.pdf") is None

    async def test_survives_bytes_that_are_not_text(self, storage) -> None:
        content = bytes(range(256)) * 8

        await storage.upload("reports/a.pdf", content, content_type="application/pdf")

        assert await storage.download("reports/a.pdf") == content

    async def test_a_second_instance_reads_what_the_first_wrote(self, bucket_name: str) -> None:
        """The whole reason this class exists.

        Two instances stand in for two Cloud Run replicas. LocalStorage passes
        every other test in this file and fails this one in production —
        silently, because the container filesystem is per-instance and discarded
        at scale-to-zero, so the report cache would re-render on every replica
        and lose everything on the way down without ever raising.
        """
        from app.utils.storage import GcsStorage

        writer = GcsStorage(bucket_name)
        reader = GcsStorage(bucket_name)

        await writer.upload("reports/a.pdf", b"rendered once", content_type="application/pdf")

        assert await reader.download("reports/a.pdf") == b"rendered once"

    def test_satisfies_the_protocol(self, storage) -> None:
        assert isinstance(storage, Storage)


class TestKeyGuards:
    """A bucket is flat, so none of LocalStorage's containment reasoning
    applies — `../x` is a legal object name here. The guard is kept because the
    same key is written to a filesystem in development and lands in a
    Content-Disposition header on the way out, and a key that is safe in one
    backend and not the other is a bug that only shows up in production.
    """

    @pytest.mark.parametrize(
        "key",
        [
            "/absolute.pdf",
            "\\windows-rooted.pdf",
            "C:/drive.pdf",
            "../escape.pdf",
            "reports/../../escape.pdf",
            "",
            "  leading-space.pdf",
            "trailing-space.pdf  ",
        ],
    )
    async def test_rejects_a_dangerous_key(self, storage, key: str) -> None:
        with pytest.raises(UnsafeStorageKey):
            await storage.upload(key, b"x", content_type="application/pdf")

    async def test_a_leading_slash_is_rejected_on_every_platform(self, storage) -> None:
        """The bug this file found. Path('/x').is_absolute() is True on Linux
        and False on Windows, where absoluteness needs a drive — so a guard
        written with a bare Path passed locally and would have let the key
        through in the container, or the reverse."""
        with pytest.raises(UnsafeStorageKey):
            await storage.upload("/abs.pdf", b"x", content_type="application/pdf")

    async def test_download_is_guarded_too(self, storage) -> None:
        """A read is the less alarming direction, but an unguarded key would
        still fetch whatever it named."""
        with pytest.raises(UnsafeStorageKey):
            await storage.download("../escape.pdf")

    async def test_nested_keys_are_fine(self, storage) -> None:
        """Slashes are how a bucket gets its only structure — the guard must not
        cost that."""
        await storage.upload("reports/2026/08/a.pdf", b"x", content_type="application/pdf")

        assert await storage.download("reports/2026/08/a.pdf") == b"x"

    async def test_nothing_is_written_when_a_key_is_rejected(self, storage) -> None:
        with pytest.raises(UnsafeStorageKey):
            await storage.upload("../escape.pdf", b"x", content_type="application/pdf")

        assert await storage.download("escape.pdf") is None
