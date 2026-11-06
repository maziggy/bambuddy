"""Serving the frontend build's /assets directory (#3175).

Plain ``StaticFiles`` sends every file uncompressed and with no
Cache-Control, so a phone downloads the whole bundle in full and the
browser asks again on every load. This subclass adds two things:

- gzip for text assets when the browser accepts it. Each file is
  compressed once and kept in memory, keyed by path, size and mtime: Vite
  never changes a hashed file in place, and a rebuild that does replace one
  changes the key. Compressing per request rather than shipping ``.gz``
  copies keeps ``static/`` (tracked in git) at its current size.
- ``immutable`` caching for the files Vite names by content hash. The
  pdf.js runtime data under ``assets/pdfjs/`` keeps its upstream names, so
  it is revalidated instead.

Only /assets uses this. An app-wide compression middleware would also
reach the camera streams and the WebSocket.
"""

from __future__ import annotations

import gzip
import os
import re
from collections import OrderedDict

import anyio
from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope

# Vite's output name: <name>-<8-char hash>.<ext>, directly in assets/.
_HASHED_ASSET_RE = re.compile(r"^[^/\\]+-[A-Za-z0-9_-]{8}\.[A-Za-z0-9]+$")

IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
REVALIDATE_CACHE_CONTROL = "no-cache"

_COMPRESSIBLE_SUFFIXES = (".js", ".mjs", ".css", ".json", ".svg", ".map", ".wasm", ".txt", ".html")
# Below this the gzip header costs more than it saves.
_MIN_COMPRESS_SIZE = 1024
# The whole build gzips to a few MB; the cap only matters if a pile of
# rebuilds runs against one long-lived process.
_CACHE_MAX_BYTES = 64 * 1024 * 1024


def accepts_gzip(accept_encoding: str | None) -> bool:
    """Whether an Accept-Encoding header allows gzip (q=0 refuses it)."""
    if not accept_encoding:
        return False
    qualities: dict[str, float] = {}
    for part in accept_encoding.split(","):
        coding, *params = part.strip().split(";")
        q = 1.0
        for param in params:
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        qualities[coding.strip().lower()] = q
    if "gzip" in qualities:
        return qualities["gzip"] > 0
    return qualities.get("*", 0.0) > 0


class _GzipCache:
    """Compressed bodies, least recently used dropped first."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._entries: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()
        self._size = 0

    async def get(self, path: str, stat_result: os.stat_result) -> bytes:
        key = (path, stat_result.st_size, stat_result.st_mtime_ns)
        body = self._entries.get(key)
        if body is not None:
            self._entries.move_to_end(key)
            return body
        body = await anyio.to_thread.run_sync(_gzip_file, path)
        # Two first requests for one file can both get here; the second
        # simply replaces the first's entry.
        if key in self._entries:
            self._size -= len(self._entries.pop(key))
        if len(body) <= self.max_bytes:
            self._entries[key] = body
            self._size += len(body)
            while self._size > self.max_bytes:
                _, dropped = self._entries.popitem(last=False)
                self._size -= len(dropped)
        return body

    def clear(self) -> None:
        self._entries.clear()
        self._size = 0


def _gzip_file(path: str) -> bytes:
    with open(path, "rb") as f:
        # mtime=0 keeps the output identical for identical input.
        return gzip.compress(f.read(), compresslevel=6, mtime=0)


_gzip_cache = _GzipCache(_CACHE_MAX_BYTES)


def _gzip_etag(etag: str) -> str:
    """The gzip variant's ETag. It must differ from the identity one, or a
    cache could answer a gzip request with the uncompressed body."""
    return etag[:-1] + '-gzip"' if etag.endswith('"') else etag + "-gzip"


class AssetStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code not in (200, 304):
            return response

        response.headers["Cache-Control"] = (
            IMMUTABLE_CACHE_CONTROL if _HASHED_ASSET_RE.match(path) else REVALIDATE_CACHE_CONTROL
        )
        if not path.lower().endswith(_COMPRESSIBLE_SUFFIXES):
            return response
        response.headers["Vary"] = "Accept-Encoding"

        if (
            not isinstance(response, FileResponse)
            or response.status_code != 200
            or scope["method"] != "GET"
            or response.stat_result is None
            or response.stat_result.st_size < _MIN_COMPRESS_SIZE
        ):
            return response
        request_headers = Headers(scope=scope)
        # Ranges are byte offsets into the identity body; leave them to FileResponse.
        if "range" in request_headers or not accepts_gzip(request_headers.get("accept-encoding")):
            return response

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("accept-ranges", None)
        headers["content-encoding"] = "gzip"
        etag = headers.get("etag")
        if etag:
            headers["etag"] = _gzip_etag(etag)
            if_none_match = request_headers.get("if-none-match")
            if if_none_match and headers["etag"] in [t.strip().removeprefix("W/") for t in if_none_match.split(",")]:
                return NotModifiedResponse(Headers(headers))

        body = await _gzip_cache.get(str(response.path), response.stat_result)
        return Response(content=body, status_code=200, headers=headers)
