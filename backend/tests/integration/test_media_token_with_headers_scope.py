"""A media token decides both who the caller is and which printers they see."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_headers_next_to_a_media_token_do_not_widen_its_printers(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    theirs = await archive_factory(b.id, print_name="Theirs")
    ours = await archive_factory(a.id, print_name="Ours")
    token = await _admin_token(async_client)
    perms = await _group(async_client, token, "perms", permissions=["printers:read", "archives:read_all"])
    team = await _group(async_client, token, "team", printer_ids=[a.id])
    member_jwt, _ = await _user(async_client, token, "member", [perms, team])
    media = await async_client.post("/api/v1/auth/media-token", headers=_auth(member_jwt))
    assert media.status_code == 200, media.text
    key = await async_client.post(
        "/api/v1/api-keys/", headers=_auth(token), json={"name": "k", "can_read_status": True}
    )
    assert key.status_code == 200, key.text
    headers = {"X-API-Key": key.json()["key"]}

    def qrcode(archive_id: int):
        return async_client.get(
            f"/api/v1/archives/{archive_id}/qrcode", params={"token": media.json()["token"]}, headers=headers
        )

    assert (await qrcode(ours.id)).status_code == 200
    assert (await qrcode(theirs.id)).status_code == 404


async def test_a_media_token_next_to_headers_does_not_widen_their_printers(
    async_client: AsyncClient, printer_factory, archive_factory
):
    a, b = await printer_factory(name="A"), await printer_factory(name="B")
    theirs = await archive_factory(b.id, print_name="Theirs")
    ours = await archive_factory(a.id, print_name="Ours")
    token = await _admin_token(async_client)
    perms = await _group(async_client, token, "perms", permissions=["printers:read", "archives:read_all"])
    team = await _group(async_client, token, "team", printer_ids=[a.id])
    member_jwt, _ = await _user(async_client, token, "member", [perms, team])
    admin_media = (await async_client.post("/api/v1/auth/media-token", headers=_auth(token))).json()["token"]

    def get(archive_id: int):
        return async_client.get(
            f"/api/v1/archives/{archive_id}", params={"token": admin_media}, headers=_auth(member_jwt)
        )

    assert (await get(ours.id)).status_code == 200
    assert (await get(theirs.id)).status_code == 404
