"""A count on an external sidebar link, set by the app it points to.

The contract these tests pin:

  ``PUT /external-links/badge`` with ``{url, count}`` sets the count on every
  sidebar link pointing at that address (same origin and path, trailing slash
  ignored), e.g. Bambuddy Orders with its To do count. Callers need what
  ``POST /notifications/app-message`` needs: an API key with the "Send
  notifications" permission, or a user with ``notifications:update``. The
  list of links carries the count while it is fresh (set within the last ten
  minutes), so an app that stopped reporting doesn't leave an old number.
  Setting it doesn't touch the link's own ``updated_at``.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.external_link import ExternalLink, ExternalLinkBadge
from backend.app.models.user import User
from backend.app.utils.local_time import utcnow_naive

URL = "/api/v1/external-links/badge"


async def _admin_token(client: AsyncClient) -> str:
    await client.post(
        "/api/v1/auth/setup",
        json={
            "auth_enabled": True,
            "admin_username": "badgeadmin",
            "admin_password": "AdminPass1!",  # pragma: allowlist secret
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "badgeadmin", "password": "AdminPass1!"},  # pragma: allowlist secret
    )
    return login.json()["access_token"]


async def _key(db: AsyncSession, *, allowed: bool) -> str:
    owner = (await db.execute(select(User).where(User.username == "badgeadmin"))).scalar_one()
    full_key, key_hash, key_prefix = generate_api_key()
    db.add(
        APIKey(
            name="Bambuddy Orders",
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=owner.id,
            can_send_notifications=allowed,
        )
    )
    await db.commit()
    return full_key


async def _link(db: AsyncSession, url: str, name: str = "Orders") -> ExternalLink:
    link = ExternalLink(name=name, url=url, icon="Package", sort_order=0)
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


async def _listed(client: AsyncClient, token: str) -> dict[str, int | None]:
    response = await client.get("/api/v1/external-links/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return {link["name"]: link["badge_count"] for link in response.json()}


class TestBadge:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_app_sets_the_count_on_the_link_pointing_at_it(self, async_client: AsyncClient, db_session):
        token = await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        await _link(db_session, "http://orders.local:8090/")
        await _link(db_session, "http://other.local:8090/", name="Other")

        response = await async_client.put(
            URL, json={"url": "http://orders.local:8090", "count": 7}, headers={"X-API-Key": key}
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"links": 1}
        assert await _listed(async_client, token) == {"Orders": 7, "Other": None}

        await async_client.put(URL, json={"url": "http://orders.local:8090/", "count": 0}, headers={"X-API-Key": key})
        assert (await _listed(async_client, token))["Orders"] == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_path_counts_too(self, async_client: AsyncClient, db_session):
        token = await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        await _link(db_session, "https://shop.example/orders/")
        await _link(db_session, "https://shop.example/print", name="Print")

        await async_client.put(URL, json={"url": "https://shop.example/orders", "count": 2}, headers={"X-API-Key": key})
        assert await _listed(async_client, token) == {"Orders": 2, "Print": None}

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_no_link_pointing_there_is_a_404(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        await _link(db_session, "http://orders.local:8090/")
        response = await async_client.put(
            URL, json={"url": "http://orders.local:9999", "count": 1}, headers={"X-API-Key": key}
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_an_old_count_is_not_shown(self, async_client: AsyncClient, db_session):
        token = await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        await _link(db_session, "http://orders.local:8090")
        await async_client.put(URL, json={"url": "http://orders.local:8090", "count": 3}, headers={"X-API-Key": key})
        await db_session.execute(update(ExternalLinkBadge).values(updated_at=utcnow_naive() - timedelta(minutes=11)))
        await db_session.commit()
        assert (await _listed(async_client, token))["Orders"] is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_link_itself_is_not_changed(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        link = await _link(db_session, "http://orders.local:8090")
        before = link.updated_at
        await async_client.put(URL, json={"url": "http://orders.local:8090", "count": 3}, headers={"X-API-Key": key})
        stored = await db_session.execute(select(ExternalLink.updated_at).where(ExternalLink.id == link.id))
        assert stored.scalar_one() == before

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deleting_the_link_drops_its_count(self, async_client: AsyncClient, db_session):
        token = await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        link = await _link(db_session, "http://orders.local:8090")
        await async_client.put(URL, json={"url": "http://orders.local:8090", "count": 3}, headers={"X-API-Key": key})
        response = await async_client.delete(
            f"/api/v1/external-links/{link.id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert (await db_session.execute(select(ExternalLinkBadge))).scalars().all() == []

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_needs_the_send_notifications_permission(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        key = await _key(db_session, allowed=False)
        await _link(db_session, "http://orders.local:8090")
        body = {"url": "http://orders.local:8090", "count": 1}
        assert (await async_client.put(URL, json=body, headers={"X-API-Key": key})).status_code == 403
        assert (await async_client.put(URL, json=body)).status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_refuses_nonsense(self, async_client: AsyncClient, db_session):
        await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        for body in ({"url": "ftp://orders.local", "count": 1}, {"url": "http://orders.local", "count": -1}):
            assert (await async_client.put(URL, json=body, headers={"X-API-Key": key})).status_code == 422


class TestMatching:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_the_same_address_written_differently_still_matches(self, async_client: AsyncClient, db_session):
        token = await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        await _link(db_session, "http://orders.local", name="Default port")
        await _link(db_session, "https://user:pass@shop.example/", name="With login")  # pragma: allowlist secret
        await _link(db_session, "http://ORDERS.local:80/", name="Spelled out")

        response = await async_client.put(
            URL, json={"url": "http://orders.local:80", "count": 4}, headers={"X-API-Key": key}
        )
        assert response.json() == {"links": 2}
        await async_client.put(URL, json={"url": "https://shop.example:443", "count": 5}, headers={"X-API-Key": key})
        assert await _listed(async_client, token) == {"Default port": 4, "With login": 5, "Spelled out": 4}


class TestWhoMaySet:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_signed_in_user_needs_notifications_update(self, async_client: AsyncClient, db_session):
        token = await _admin_token(async_client)
        await _link(db_session, "http://orders.local:8090")
        response = await async_client.put(
            URL, json={"url": "http://orders.local:8090", "count": 2}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_key_can_do_no_more_than_its_owner(self, async_client: AsyncClient, db_session):
        from backend.app.models.group import Group

        await _admin_token(async_client)
        viewers = (await db_session.execute(select(Group).where(Group.name == "Viewers"))).scalar_one()
        owner = User(username="viewer", password_hash="x", role="user", is_active=True)
        owner.groups = [viewers]
        db_session.add(owner)
        await db_session.commit()
        full_key, key_hash, key_prefix = generate_api_key()
        db_session.add(
            APIKey(name="k", key_hash=key_hash, key_prefix=key_prefix, user_id=owner.id, can_send_notifications=True)
        )
        await db_session.commit()
        await _link(db_session, "http://orders.local:8090")
        response = await async_client.put(
            URL, json={"url": "http://orders.local:8090", "count": 2}, headers={"X-API-Key": full_key}
        )
        assert response.status_code == 403


class TestLinkGone:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_link_removed_any_way_takes_its_count_along(self, async_client: AsyncClient, db_session):
        """Not only through the delete route: SQLite applies no ON DELETE by itself."""
        token = await _admin_token(async_client)
        key = await _key(db_session, allowed=True)
        link = await _link(db_session, "http://orders.local:8090")
        await async_client.put(URL, json={"url": "http://orders.local:8090", "count": 3}, headers={"X-API-Key": key})
        await db_session.delete(await db_session.get(ExternalLink, link.id))
        await db_session.commit()
        assert (await db_session.execute(select(ExternalLinkBadge))).scalars().all() == []
        await _link(db_session, "http://other.local:8090", name="Next")
        assert await _listed(async_client, token) == {"Next": None}
