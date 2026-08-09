"""Tests for the two cloud-portability boundaries.

The path-traversal cases matter beyond the abstraction itself: storage keys will
be built from scan ids, and this is the one place a filesystem path is derived
from data.
"""

import os.path
from pathlib import Path

import pytest

from app.utils.queue import InMemoryQueue, Queue, get_queue, set_queue
from app.utils.storage import (
    LocalStorage,
    NullStorage,
    Storage,
    StorageUnavailable,
    UnsafeStorageKey,
    get_storage,
    set_storage,
)


class TestInMemoryQueue:
    async def test_publish_records_the_job(self) -> None:
        queue = InMemoryQueue()

        await queue.publish("run_scan", scan_id="abc")

        assert queue.published == [("run_scan", {"scan_id": "abc"})]

    async def test_publish_returns_an_opaque_id(self) -> None:
        queue = InMemoryQueue()

        first = await queue.publish("run_scan", scan_id="abc")
        second = await queue.publish("run_scan", scan_id="abc")

        assert first != second

    async def test_clear_empties_the_record(self) -> None:
        queue = InMemoryQueue()
        await queue.publish("run_scan", scan_id="abc")

        queue.clear()

        assert queue.published == []

    def test_satisfies_the_protocol(self) -> None:
        """The Protocol is what services/ depends on — a replacement backend
        only has to match this."""
        assert isinstance(InMemoryQueue(), Queue)

    def test_the_queue_is_swappable(self) -> None:
        original = get_queue()
        replacement = InMemoryQueue()
        try:
            set_queue(replacement)
            assert get_queue() is replacement
        finally:
            set_queue(original)


class TestLocalStorage:
    async def test_upload_writes_the_content(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path)

        location = await storage.upload("report.pdf", b"%PDF-1.7", content_type="application/pdf")

        assert Path(location).read_bytes() == b"%PDF-1.7"

    async def test_upload_creates_missing_directories(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path)

        location = await storage.upload(
            "scans/abc/report.pdf", b"data", content_type="application/pdf"
        )

        assert Path(location).exists()

    async def test_upload_overwrites(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path)
        await storage.upload("report.pdf", b"old", content_type="application/pdf")

        location = await storage.upload("report.pdf", b"new", content_type="application/pdf")

        assert Path(location).read_bytes() == b"new"

    @pytest.mark.parametrize(
        "key",
        [
            "../escaped.pdf",
            "../../etc/passwd",
            "scans/../../escaped.pdf",
        ],
    )
    async def test_rejects_keys_that_escape_the_root(self, tmp_path: Path, key: str) -> None:
        storage = LocalStorage(tmp_path / "root")

        with pytest.raises(UnsafeStorageKey):
            await storage.upload(key, b"data", content_type="application/pdf")

    async def test_rejects_an_absolute_key(self, tmp_path: Path) -> None:
        """Path joining silently discards the root when the second operand is
        absolute, so this would otherwise write anywhere on disk."""
        storage = LocalStorage(tmp_path)

        with pytest.raises(UnsafeStorageKey):
            await storage.upload(
                str(tmp_path / "elsewhere.pdf"), b"data", content_type="application/pdf"
            )

    async def test_nothing_is_written_when_a_key_is_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        storage = LocalStorage(root)

        with pytest.raises(UnsafeStorageKey):
            await storage.upload("../escaped.pdf", b"data", content_type="application/pdf")

        assert not (tmp_path / "escaped.pdf").exists()

    async def test_allows_nested_keys_inside_the_root(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path)

        location = await storage.upload("a/b/c/report.pdf", b"data", content_type="application/pdf")

        assert Path(location).is_relative_to(tmp_path.resolve())

    async def test_uploads_where_isreserved_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulates Linux, where posixpath has no isreserved at all.

        This suite runs on Windows, where os.path.isreserved exists — so an
        unguarded call passes here and raises AttributeError in the container,
        which is the only place the code actually runs. Removing the attribute
        reproduces the deployment platform on the development one.
        """
        monkeypatch.delattr(os.path, "isreserved", raising=False)
        storage = LocalStorage(tmp_path)

        location = await storage.upload("report.pdf", b"data", content_type="application/pdf")

        assert Path(location).read_bytes() == b"data"

    async def test_download_returns_what_was_uploaded(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path)
        await storage.upload("scans/abc/report.pdf", b"%PDF-1.7", content_type="application/pdf")

        assert await storage.download("scans/abc/report.pdf") == b"%PDF-1.7"

    async def test_download_returns_none_for_a_missing_key(self, tmp_path: Path) -> None:
        """A miss is an ordinary answer, not a failure — the caller this exists
        for is a cache read."""
        storage = LocalStorage(tmp_path)

        assert await storage.download("scans/never/report.pdf") is None

    async def test_download_returns_none_for_a_directory(self, tmp_path: Path) -> None:
        storage = LocalStorage(tmp_path)
        await storage.upload("scans/abc/report.pdf", b"data", content_type="application/pdf")

        assert await storage.download("scans/abc") is None

    @pytest.mark.parametrize("key", ["../../etc/passwd", "scans/../../secret.pdf"])
    async def test_download_rejects_keys_that_escape_the_root(
        self, tmp_path: Path, key: str
    ) -> None:
        """Reading is the less alarming direction and still serves an arbitrary
        file on disk to whoever asked for it."""
        storage = LocalStorage(tmp_path / "root")

        with pytest.raises(UnsafeStorageKey):
            await storage.download(key)

    def test_satisfies_the_protocol(self, tmp_path: Path) -> None:
        assert isinstance(LocalStorage(tmp_path), Storage)


class TestNullStorage:
    """The default. Its job is to refuse rather than to silently discard."""

    async def test_upload_raises(self) -> None:
        with pytest.raises(StorageUnavailable):
            await NullStorage().upload("report.pdf", b"data", content_type="application/pdf")

    async def test_download_raises_rather_than_reporting_a_miss(self) -> None:
        """Returning None here would have a cache read miss forever and
        re-render on every request, hiding the misconfiguration."""
        with pytest.raises(StorageUnavailable):
            await NullStorage().download("report.pdf")

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(NullStorage(), Storage)

    def test_is_the_default(self) -> None:
        """Nothing installs storage in the suite — lifespan does not run under
        ASGITransport — so an unconfigured process refuses."""
        assert isinstance(get_storage(), NullStorage)

    def test_the_storage_is_swappable(self, tmp_path: Path) -> None:
        original = get_storage()
        replacement = LocalStorage(tmp_path)
        try:
            set_storage(replacement)
            assert get_storage() is replacement
        finally:
            set_storage(original)
