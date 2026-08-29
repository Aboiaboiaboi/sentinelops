"""Tests for the S3 backend.

Split from test_utils.py because these need a server, the same arrangement as
test_gcs_storage.py. They run against **MinIO**, a real implementation of the
S3 API, not against a mock of boto3 — a mock would assert that this code calls
the methods this code calls, which is the shape of test that passes while the
feature is broken.

Start the server with:

    docker run --rm -p 9000:9000 -e MINIO_ROOT_USER=minioadmin \
        -e MINIO_ROOT_PASSWORD=minioadmin minio/minio server /data

Without it every test here skips, so the suite stays runnable with no Docker —
the same arrangement as the sandbox integration test.
"""

import os
import socket
import uuid

import pytest

from app.utils.storage import Storage, UnsafeStorageKey

# boto3 lives in the optional `s3` dependency group, so an install without it
# must skip rather than error — that is the whole point of the group.
# `uv sync --all-groups` installs it, which is what CI does.
pytest.importorskip("boto3", reason="the s3 dependency group is not installed")

ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000")

# MinIO's own defaults, used only against the local/CI emulator above — never
# credentials for a real account. Set before any client is built, the same
# ordering test_gcs_storage.py uses for STORAGE_EMULATOR_HOST.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")


def _emulator_is_up() -> bool:
    host, _, port = ENDPOINT_URL.removeprefix("http://").removeprefix("https://").partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _emulator_is_up(), reason=f"no S3 emulator listening on {ENDPOINT_URL}"
)


@pytest.fixture
def bucket_name() -> str:
    """A fresh bucket per test, so one test's objects cannot answer another's
    download."""
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=ENDPOINT_URL,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    name = f"sentinelops-test-{uuid.uuid4().hex[:12]}"
    client.create_bucket(Bucket=name)
    return name


@pytest.fixture
def storage(bucket_name: str):
    from app.utils.storage import S3Storage

    return S3Storage(bucket_name, endpoint_url=ENDPOINT_URL, region="us-east-1")


class TestRoundTrip:
    async def test_upload_then_download_returns_the_same_bytes(self, storage) -> None:
        await storage.upload("reports/a.pdf", b"%PDF-1.4 body", content_type="application/pdf")

        assert await storage.download("reports/a.pdf") == b"%PDF-1.4 body"

    async def test_upload_returns_an_s3_locator(self, storage, bucket_name: str) -> None:
        """Opaque to callers and stored as given, so it must not be a signed or
        public URL — those expire, and one of them would leak."""
        locator = await storage.upload("reports/a.pdf", b"x", content_type="application/pdf")

        assert locator == f"s3://{bucket_name}/reports/a.pdf"

    async def test_the_content_type_reaches_the_object(self, storage, bucket_name: str) -> None:
        """It is response metadata on the way back out. LocalStorage has nowhere
        to put it and drops it; here it has to survive."""
        import boto3
        from botocore.config import Config

        await storage.upload("reports/a.pdf", b"x", content_type="application/pdf")

        client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT_URL,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}),
        )
        head = client.head_object(Bucket=bucket_name, Key="reports/a.pdf")
        assert head["ContentType"] == "application/pdf"

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

        Two instances stand in for two replicas behind a load balancer.
        LocalStorage passes every other test in this file and fails this one in
        production — silently, because a per-instance filesystem discards
        everything on the way down without ever raising.
        """
        from app.utils.storage import S3Storage

        writer = S3Storage(bucket_name, endpoint_url=ENDPOINT_URL, region="us-east-1")
        reader = S3Storage(bucket_name, endpoint_url=ENDPOINT_URL, region="us-east-1")

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
        """The same Windows/POSIX absoluteness bug test_gcs_storage.py found —
        `_check_flat_key` is shared between both classes, so this exercises the
        same guard through a different door."""
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
