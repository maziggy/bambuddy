"""Logo persistence, image validation, and overlay authentication."""

import io

import pytest
from PIL import Image

from backend.tests.overlay_helpers import mint_token, setup_admin

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def logo_bytes(fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGBA", (80, 40), (255, 0, 0, 128)).save(output, format=fmt)
    return output.getvalue()


async def test_logo_upload_read_remove(async_client, tmp_path, monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    response = await async_client.post(
        "/api/v1/settings/overlay-logo", files={"file": ("logo.webp", logo_bytes("WEBP"), "image/webp")}
    )
    assert response.status_code == 200
    image = await async_client.get("/api/v1/overlay-branding/logo")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(image.content)) as decoded:
        assert decoded.size == (80, 40)
        assert decoded.mode == "RGBA"
    assert (await async_client.delete("/api/v1/settings/overlay-logo")).status_code == 200
    assert (await async_client.get("/api/v1/overlay-branding/logo")).status_code == 404


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"<svg></svg>", 400), (b"invalid", 400), (b"x" * (2 * 1024 * 1024 + 1), 413)],
    ids=["svg", "invalid", "oversized"],
)
async def test_rejects_invalid_uploads(async_client, tmp_path, monkeypatch, content, status):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    response = await async_client.post(
        "/api/v1/settings/overlay-logo", files={"file": ("logo.png", content, "image/png")}
    )
    assert response.status_code == status
    assert not (tmp_path / "overlay-branding" / "logo.png").exists()


async def test_logo_auth_and_token_scope(async_client, tmp_path, monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    jwt = await setup_admin(async_client, suffix="_branding")
    headers = {"Authorization": f"Bearer {jwt}"}
    response = await async_client.post(
        "/api/v1/settings/overlay-logo",
        headers=headers,
        files={"file": ("logo.png", logo_bytes(), "image/png")},
    )
    assert response.status_code == 200
    assert (await async_client.get("/api/v1/settings/overlay-logo", headers=headers)).status_code == 200
    assert (await async_client.get("/api/v1/settings/overlay-logo")).status_code == 401
    assert (await async_client.get("/api/v1/overlay-branding/logo")).status_code == 401
    for scope in ("camera_stream", "camwall", "overlay"):
        token = await mint_token(async_client, jwt, scope=scope, name=scope)
        response = await async_client.get("/api/v1/overlay-branding/logo", params={"token": token})
        assert response.status_code == (200 if scope == "overlay" else 401)
        assert (await async_client.delete("/api/v1/settings/overlay-logo", params={"token": token})).status_code == 401
    assert (await async_client.get("/api/v1/overlay-branding/logo?token=invalid")).status_code == 401


async def test_invalid_replacement_preserves_logo(async_client, tmp_path, monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "base_dir", tmp_path)
    await async_client.post("/api/v1/settings/overlay-logo", files={"file": ("logo.png", logo_bytes(), "image/png")})
    before = (await async_client.get("/api/v1/overlay-branding/logo")).content
    output = io.BytesIO()
    Image.new("RGB", (2100, 2100)).save(output, "PNG")
    response = await async_client.post(
        "/api/v1/settings/overlay-logo", files={"file": ("huge.png", output.getvalue(), "image/png")}
    )
    assert response.status_code == 400
    assert (await async_client.get("/api/v1/overlay-branding/logo")).content == before
