"""Shared test data and mock builders for OIDC icon tests (#1333).

Cross-imported from ``backend.tests.unit.*`` and ``backend.tests.integration.*``
following the established pattern (see ``backend/tests/unit/services/conftest.py``
which imports ``mock_ftp_server``).

Two mock builders are provided because ``fetch_icon`` evolved from a single
``client.get(...)`` call into a streaming ``client.stream(...).aiter_bytes()``
loop:

* ``build_get_icon_mock`` — the pre-streaming pattern, kept for tests that
  exercise httpx.AsyncClient.get() directly (e.g. routes that use httpx
  outside the icon-fetcher).
* ``build_streaming_icon_mock`` — the current ``fetch_icon`` pattern; tests
  that exercise the size-cap early-exit need this.

Both produce ``(MockHttpxClient, call_recorder)`` tuples for patching.
"""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

# Tiny valid 1×1 transparent PNG (~70 bytes) — small enough to fit in one
# 4 KB chunk during streaming tests; suitable for the happy-path everywhere.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100"
    "0d0a2db40000000049454e44ae426082"
)
PNG_ETAG = hashlib.sha256(PNG_BYTES).hexdigest()


def build_get_icon_mock(
    *,
    body: bytes = PNG_BYTES,
    content_type: str | None = "image/png",
    status_code: int = 200,
):
    """Returns ``(MockHttpxClient, mock_get)`` for the pre-streaming pattern.

    ``mock_get`` is an ``AsyncMock`` so tests can assert call count and
    inspect kwargs (e.g. ``follow_redirects=False``).

    Passing ``content_type=None`` produces a response with no Content-Type
    header at all (distinct from ``""``) — used to exercise the missing-
    header branch.
    """
    headers: dict[str, str] = {"content-type": content_type} if content_type is not None else {}
    response = SimpleNamespace(status_code=status_code, headers=headers, content=body)
    mock_get = AsyncMock(return_value=response)

    class _MockHttpxClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return SimpleNamespace(get=mock_get)

        async def __aexit__(self, *_exc):
            return False

    return _MockHttpxClient, mock_get


def build_streaming_icon_mock(
    *,
    body: bytes = PNG_BYTES,
    content_type: str | None = "image/png",
    status_code: int = 200,
    chunk_size: int = 4096,
):
    """Returns ``(MockHttpxClient, stream_recorder)`` for the current
    ``client.stream("GET", url, follow_redirects=...)`` + ``aiter_bytes()`` path.

    ``stream_recorder`` is an ``AsyncMock`` that records every ``.stream()``
    call so tests can assert e.g. ``follow_redirects=False`` was passed.

    ``body`` is emitted in ``chunk_size``-byte chunks. Tests of the size-cap
    early-exit should pick a chunk_size that crosses the cap mid-stream
    (e.g. body = 2 MB, chunk_size = 4096 → cap fires after ~256 chunks
    without buffering the whole payload).
    """
    headers: dict[str, str] = {"content-type": content_type} if content_type is not None else {}
    stream_recorder = AsyncMock()

    async def _aiter_bytes():
        for i in range(0, len(body), chunk_size):
            yield body[i : i + chunk_size]

    response = SimpleNamespace(
        status_code=status_code,
        headers=headers,
        aiter_bytes=_aiter_bytes,
    )

    class _StreamCtx:
        def __init__(self, *args, **kwargs):
            # Record the .stream() call positional + keyword args so tests
            # can assert `follow_redirects=False` etc.
            stream_recorder(*args, **kwargs)

        async def __aenter__(self):
            return response

        async def __aexit__(self, *_exc):
            return False

    class _MockHttpxClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def stream(self, *args, **kwargs):
            return _StreamCtx(*args, **kwargs)

    return _MockHttpxClient, stream_recorder
