"""Unit tests for the shared external-catalog cache spine.

Both catalog clients (OFD, SpoolmanDB-Community) run on
``external_catalog_cache.CachedCatalogClient``; these tests lock the shared
guarantees once, against a minimal fake subclass, so the per-client test
files don't each have to re-prove the whole cache matrix:

- fresh disk cache served without a download; stale/wrong-version triggers one
- refresh failure falls back to a stale cache; with nothing on disk it raises
- successful refresh writes the cache atomically (temp file + rename) in the
  versioned wrapper shape; a failed write still serves the in-memory result
- seed fallback: used only when no cache exists and the refresh fails, and on
  use it persists into DATA_DIR keeping the seed's own built_at (so later
  boots read it as a normal stale cache); corrupt/wrong-version seeds are
  ignored
- in-memory TTL short-circuits disk reads; concurrent loads download once
- stream_download enforces its running byte cap
"""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from backend.app.services.external_catalog_cache import CachedCatalogClient, stream_download


class _FakeClient(CachedCatalogClient):
    """Minimal concrete catalog: a controllable _download_and_build and an
    optional seed path, so every base-class rung can be driven directly."""

    name = "fake-catalog"
    cache_filename = "fake_catalog_cache.json"
    cache_version = 7

    def __init__(self) -> None:
        super().__init__()
        self.download_calls = 0
        # Default to raising so any test that expects "no download happens"
        # fails loudly if one does.
        self.download_result: dict | Exception = AssertionError("unexpected download")
        self.download_delay = 0.0
        self._seed_path: Path | None = None

    async def _download_and_build(self) -> dict:
        self.download_calls += 1
        if self.download_delay:
            await asyncio.sleep(self.download_delay)
        if isinstance(self.download_result, Exception):
            raise self.download_result
        return dict(self.download_result)

    def seed_path(self) -> Path | None:
        return self._seed_path


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """resolve_data_dir() reads DATA_DIR fresh per call, so this redirects
    every cache read/write into the test's own tmp_path."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


@pytest.fixture
def client():
    return _FakeClient()


def _write_cache_file(tmp_path, payload, *, built_at=None, version=None):
    (tmp_path / _FakeClient.cache_filename).write_text(
        json.dumps(
            {
                "cache_version": _FakeClient.cache_version if version is None else version,
                "built_at": time.time() if built_at is None else built_at,
                "payload": payload,
            }
        )
    )


def _stale_time(client: _FakeClient) -> float:
    return time.time() - client.ttl_seconds - 10


class TestEnsureLoaded:
    @pytest.mark.asyncio
    async def test_fresh_disk_cache_used_without_download(self, tmp_path, client):
        _write_cache_file(tmp_path, {"gtin_index": {"1": "x"}})

        payload = await client.payload()

        assert payload == {"gtin_index": {"1": "x"}}
        assert client.download_calls == 0

    @pytest.mark.asyncio
    async def test_stale_disk_cache_triggers_refresh(self, tmp_path, client):
        _write_cache_file(tmp_path, {"old": True}, built_at=_stale_time(client))
        client.download_result = {"new": True}

        payload = await client.payload()

        assert payload == {"new": True}
        assert client.download_calls == 1

    @pytest.mark.asyncio
    async def test_wrong_cache_version_triggers_refresh(self, tmp_path, client):
        """A cache written by an older payload shape must not be misread —
        even when its built_at is perfectly fresh."""
        _write_cache_file(tmp_path, {"old_shape": True}, version=_FakeClient.cache_version - 1)
        client.download_result = {"new": True}

        payload = await client.payload()

        assert payload == {"new": True}
        assert client.download_calls == 1

    @pytest.mark.asyncio
    async def test_refresh_failure_falls_back_to_stale_disk_cache(self, tmp_path, client):
        """Offline/upstream-down must not discard an otherwise-usable, if old,
        index — a stale hit beats reporting no match for every barcode."""
        _write_cache_file(tmp_path, {"stale": True}, built_at=_stale_time(client))
        client.download_result = RuntimeError("offline")

        payload = await client.payload()

        assert payload == {"stale": True}

    @pytest.mark.asyncio
    async def test_refresh_failure_with_nothing_on_disk_raises(self, client):
        """No stale fallback and no seed (first-ever startup, no network) —
        the caller must still learn the lookup couldn't be attempted."""
        client.download_result = RuntimeError("offline")

        with pytest.raises(RuntimeError, match="offline"):
            await client.payload()

    @pytest.mark.asyncio
    async def test_successful_refresh_writes_cache_atomically(self, tmp_path, client):
        """Cache writes go through a temp file + rename, never a partial file
        at the real path — even if a write is interrupted mid-way."""
        client.download_result = {"k": "v"}

        await client.payload()

        cache_file = tmp_path / _FakeClient.cache_filename
        assert cache_file.exists()
        assert list(tmp_path.glob("*.tmp")) == []
        data = json.loads(cache_file.read_text())
        assert data["cache_version"] == _FakeClient.cache_version
        assert isinstance(data["built_at"], float)
        assert data["payload"] == {"k": "v"}

    @pytest.mark.asyncio
    async def test_write_failure_still_serves_result(self, tmp_path, client, monkeypatch):
        """A read-only DATA_DIR (or full disk) must not turn a successful
        download into a failed lookup — the write is best-effort."""

        def _boom(path, data):
            raise OSError("disk full")

        monkeypatch.setattr(client, "_write_file_sync", _boom)
        client.download_result = {"k": "v"}

        payload = await client.payload()

        assert payload == {"k": "v"}
        assert not (tmp_path / _FakeClient.cache_filename).exists()

    @pytest.mark.asyncio
    async def test_in_memory_ttl_skips_disk_reads(self, tmp_path, client, monkeypatch):
        """Within the TTL the loaded payload is served from memory — a second
        call must not re-read (or re-parse) the cache file per lookup."""
        _write_cache_file(tmp_path, {"k": "v"})
        reads = {"n": 0}
        real_read = client._read_file_sync

        def counting_read(path):
            reads["n"] += 1
            return real_read(path)

        monkeypatch.setattr(client, "_read_file_sync", counting_read)

        assert await client.payload() == {"k": "v"}
        assert reads["n"] == 1
        assert await client.payload() == {"k": "v"}
        assert reads["n"] == 1

    @pytest.mark.asyncio
    async def test_concurrent_loads_download_once(self, client):
        """Two requests hitting an expired cache at once must not both fetch
        the upstream dataset — the lock serializes and the second load reuses
        the first's result."""
        client.download_result = {"k": "v"}
        client.download_delay = 0.05

        results = await asyncio.gather(client.payload(), client.payload())

        assert results == [{"k": "v"}, {"k": "v"}]
        assert client.download_calls == 1


class TestSeedFallback:
    def _write_seed(self, path, payload, *, built_at=12345.0, version=None):
        path.write_text(
            json.dumps(
                {
                    "cache_version": _FakeClient.cache_version if version is None else version,
                    "built_at": built_at,
                    "payload": payload,
                }
            )
        )

    @pytest.mark.asyncio
    async def test_seed_used_when_no_cache_and_refresh_fails_and_persists_stale(self, tmp_path, client):
        """A fresh air-gapped install (empty DATA_DIR, no network) falls back
        to the baked-in seed — and persists it into DATA_DIR keeping the
        seed's own built_at, so later boots read it as a normal stale cache
        and keep trying to refresh past it instead of trusting it for a TTL."""
        seed = tmp_path / "seed.json"
        self._write_seed(seed, {"from_seed": True}, built_at=12345.0)
        client._seed_path = seed
        client.download_result = RuntimeError("offline")

        payload = await client.payload()

        assert payload == {"from_seed": True}
        written = json.loads((tmp_path / _FakeClient.cache_filename).read_text())
        assert written["payload"] == {"from_seed": True}
        assert written["built_at"] == 12345.0

    @pytest.mark.asyncio
    async def test_stale_cache_outranks_seed(self, tmp_path, client):
        """The seed is the last rung: a stale-but-real cache (built from a
        live refresh at some point) always beats the frozen build-time
        snapshot."""
        _write_cache_file(tmp_path, {"stale": True}, built_at=_stale_time(client))
        seed = tmp_path / "seed.json"
        self._write_seed(seed, {"from_seed": True})
        client._seed_path = seed
        client.download_result = RuntimeError("offline")

        payload = await client.payload()

        assert payload == {"stale": True}

    @pytest.mark.asyncio
    async def test_corrupt_seed_is_ignored_and_raises(self, tmp_path, client):
        seed = tmp_path / "seed.json"
        seed.write_text("{not valid json")
        client._seed_path = seed
        client.download_result = RuntimeError("offline")

        with pytest.raises(RuntimeError, match="offline"):
            await client.payload()

    @pytest.mark.asyncio
    async def test_wrong_version_seed_is_ignored_and_raises(self, tmp_path, client):
        """A seed baked by an older image (older payload shape) must not be
        misread any more than an old cache file would be."""
        seed = tmp_path / "seed.json"
        self._write_seed(seed, {"old_shape": True}, version=_FakeClient.cache_version - 1)
        client._seed_path = seed
        client.download_result = RuntimeError("offline")

        with pytest.raises(RuntimeError, match="offline"):
            await client.payload()


_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(response_bytes: bytes):
    """A drop-in replacement for httpx.AsyncClient that serves
    `response_bytes` for any request, so stream_download's real streaming and
    size-cap code runs against an in-memory response instead of the network."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=response_bytes)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return factory


class TestStreamDownload:
    @pytest.mark.asyncio
    async def test_returns_full_body_under_cap(self, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(b"hello catalog"))

        body = await stream_download("https://example.invalid/all.json", max_bytes=1024, timeout=5.0)

        assert body == b"hello catalog"

    @pytest.mark.asyncio
    async def test_body_over_cap_raises(self, monkeypatch):
        """Covers the review finding behind the caps: a large/slow upstream
        response must abort at the cap, never buffer unbounded and OOM the
        backend on the 24h auto-refresh."""
        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(b"x" * 101))

        with pytest.raises(ValueError, match="exceeded"):
            await stream_download("https://example.invalid/all.json", max_bytes=100, timeout=5.0)
