"""A camera URL's redirects are checked like the URL itself.

The configured URL goes through the camera guard, but the HTTP client followed
redirects on its own, so a camera host could send a fetch anywhere the guard
refuses: the cloud metadata endpoint, the host itself. A snapshot that is not
an image is not passed on either: it would hand whatever a URL returns to the
viewer of the camera stream.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.app.services import external_camera

JPEG = b"\xff\xd8" + b"\x00" * 16 + b"\xff\xd9"
CAMERA = "http://192.168.1.50/snapshot"


class _Response:
    def __init__(self, status: int, body: bytes = b"", location: str | None = None):
        self.status = status
        self._body = body
        self.headers = {"Location": location} if location else {}
        self.content = self

    async def read(self) -> bytes:
        return self._body

    async def iter_chunked(self, _size):
        yield self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Session:
    """aiohttp-like: follows redirects itself unless told not to."""

    def __init__(self, routes: dict[str, _Response], visited: list[str]):
        self.routes = routes
        self.visited = visited

    def get(self, url, allow_redirects=True, **_kwargs):
        self.visited.append(url)
        response = self.routes.get(url, _Response(404))
        hops = 0
        while allow_redirects and response.status in (301, 302, 303, 307, 308) and hops < 10:
            url = response.headers["Location"]
            self.visited.append(url)
            response = self.routes.get(url, _Response(404))
            hops += 1
        return response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _session(routes: dict[str, _Response]):
    visited: list[str] = []
    factory = patch.object(external_camera.aiohttp, "ClientSession", lambda *a, **k: _Session(routes, visited))
    return factory, visited


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/api/v1/settings",
        "http://metadata.google.internal/computeMetadata/v1/",
        "file:///etc/passwd",
    ],
)
@pytest.mark.parametrize("fetch", ["snapshot", "mjpeg_frame"])
async def test_a_redirect_to_a_refused_destination_is_not_followed(target, fetch):
    factory, visited = _session({CAMERA: _Response(302, location=target), target: _Response(200, JPEG)})

    with factory:
        if fetch == "snapshot":
            frame = await external_camera._capture_snapshot(CAMERA, timeout=5)
        else:
            frame = await external_camera._capture_mjpeg_frame(CAMERA, timeout=5)

    assert frame is None
    assert target not in visited


@pytest.mark.asyncio
async def test_the_live_stream_does_not_follow_a_refused_redirect():
    target = "http://169.254.169.254/latest/meta-data/"
    factory, visited = _session({CAMERA: _Response(302, location=target), target: _Response(200, JPEG + JPEG)})

    with factory:
        frames = [frame async for frame in external_camera._stream_mjpeg(CAMERA)]

    assert frames == []
    assert target not in visited


@pytest.mark.asyncio
async def test_a_redirect_the_guard_allows_is_followed():
    """A camera moving its snapshot, or to https, keeps working."""
    target = "https://192.168.1.50/snapshot.jpg"
    factory, visited = _session({CAMERA: _Response(302, location=target), target: _Response(200, JPEG)})

    with factory:
        frame = await external_camera._capture_snapshot(CAMERA, timeout=5)

    assert frame == JPEG
    assert visited == [CAMERA, target]


@pytest.mark.asyncio
async def test_a_relative_redirect_is_resolved_against_the_camera():
    factory, visited = _session(
        {CAMERA: _Response(302, location="/snap.jpg"), "http://192.168.1.50/snap.jpg": _Response(200, JPEG)}
    )

    with factory:
        frame = await external_camera._capture_snapshot(CAMERA, timeout=5)

    assert frame == JPEG


@pytest.mark.asyncio
async def test_endless_redirects_give_up():
    a, b = "http://192.168.1.50/a", "http://192.168.1.50/b"
    factory, visited = _session(
        {CAMERA: _Response(302, location=a), a: _Response(302, location=b), b: _Response(302, location=a)}
    )

    with factory:
        frame = await external_camera._capture_snapshot(CAMERA, timeout=5)

    assert frame is None
    assert len(visited) <= 7


@pytest.mark.asyncio
async def test_a_snapshot_that_is_not_an_image_is_not_passed_on():
    factory, _visited = _session({CAMERA: _Response(200, b'{"AccessKeyId": "x"}')})

    with factory:
        frame = await external_camera._capture_snapshot(CAMERA, timeout=5)

    assert frame is None


@pytest.mark.asyncio
async def test_the_camera_login_follows_a_redirect_on_the_same_camera():
    """As the HTTP client did: an absolute redirect to the same scheme, host and
    port keeps the login from the camera URL."""
    camera = "http://admin:pw@192.168.1.50/snapshot"
    factory, visited = _session(
        {
            camera: _Response(302, location="http://192.168.1.50/snap.jpg"),
            "http://admin:pw@192.168.1.50/snap.jpg": _Response(200, JPEG),
        }
    )

    with factory:
        frame = await external_camera._capture_snapshot(camera, timeout=5)

    assert frame == JPEG
    assert visited[-1] == "http://admin:pw@192.168.1.50/snap.jpg"


@pytest.mark.asyncio
async def test_the_camera_login_does_not_follow_a_redirect_elsewhere():
    camera = "http://admin:pw@192.168.1.50/snapshot"
    factory, visited = _session(
        {
            camera: _Response(302, location="http://192.168.1.60/snap.jpg"),
            "http://192.168.1.60/snap.jpg": _Response(200, JPEG),
        }
    )

    with factory:
        frame = await external_camera._capture_snapshot(camera, timeout=5)

    assert frame == JPEG
    assert visited[-1] == "http://192.168.1.60/snap.jpg"


@pytest.mark.asyncio
async def test_one_deadline_covers_every_redirect():
    """Each hop is slow but under the timeout; together they are not."""
    import asyncio

    class _Slow(_Session):
        def get(self, url, allow_redirects=True, **_kwargs):
            response = super().get(url, allow_redirects=allow_redirects)

            class _Delayed:
                async def __aenter__(self):
                    await asyncio.sleep(0.4)
                    return response

                async def __aexit__(self, *_exc):
                    return False

            return _Delayed()

    hops = {f"http://192.168.1.50/{i}": _Response(302, location=f"http://192.168.1.50/{i + 1}") for i in range(4)}
    hops["http://192.168.1.50/4"] = _Response(200, JPEG)
    visited: list[str] = []
    with patch.object(external_camera.aiohttp, "ClientSession", lambda *a, **k: _Slow(hops, visited)):
        started = asyncio.get_running_loop().time()
        frame = await external_camera._capture_snapshot("http://192.168.1.50/0", timeout=1)
        elapsed = asyncio.get_running_loop().time() - started

    assert frame is None
    assert elapsed < 1.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("camera", "location"),
    [
        (CAMERA, "http://[::1"),
        (CAMERA, "//[zz/"),
        ("http://admin:pw@192.168.1.50/snapshot", "http://192.168.1.50:99999/x"),
    ],
)
async def test_a_malformed_redirect_is_refused_not_raised(camera, location):
    factory, _visited = _session({camera: _Response(302, location=location)})

    with factory:
        frame = await external_camera._capture_snapshot(camera, timeout=5)
        frames = [f async for f in external_camera._stream_mjpeg(camera)]

    assert frame is None and frames == []
