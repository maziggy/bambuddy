"""3D Filament Profiles: connecting, lookup and spool links (#2981).

3DFP is replaced by a fake server behind httpx's MockTransport, registered as
the shared client, so the real client code runs: form fields, headers, the two
error shapes and the filament cache.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.threedfp import parse_lookup
from backend.app.core.config import APP_VERSION
from backend.app.core.permissions import DEFAULT_GROUPS, Permission
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.threedfp_spool_link import ThreeDfpSpoolLink
from backend.app.models.user import User
from backend.app.services import threedfp as service

FULL_RECORD = {
    "id": 101,
    "brand_key": "bambu-lab",
    "brand_name": "Bambu Lab",
    "material": "PLA",
    "material_type": "Basic",
    "color": "Jade White",
    "rgb": "#ffffff",
    "bambu_filament_id": "GFA00",
    "discontinued": False,
    "url": "https://3dfilamentprofiles.com/filament/details/101",
    "temp_min": 190,
    "temp_max": 230,
    "nominal_weight": 1000,
    "spool_weight": 250,
    "stores": [
        {"region": "us", "price": 19.99, "currency": "USD", "availability": "in_stock", "url": "https://3dfp.to/p/35"},
        {"region": "eu", "price": 21.5, "currency": "EUR", "url": "javascript:alert(1)"},
    ],
}
SUMMARY = {k: FULL_RECORD[k] for k in ("id", "brand_name", "material", "material_type", "color", "rgb")}


class Fake3dfp:
    """Answers like 3DFP; ``token_replies`` is consumed by each token poll."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_replies: list[tuple[int, dict]] = []
        self.me_reply: tuple[int, dict] = (
            200,
            {
                "account": {"email": "maker@example.com"},
                "plan": "free",
                "scopes": ["filaments:read", "spools:read", "spools:write"],
                "active_scopes": ["filaments:read"],
                "quota": {"limit": 100, "remaining": 97, "resets_at": "2026-12-01T00:00:00.000Z"},
                "upgrade_url": "https://3dfilamentprofiles.com/my/api",
            },
        )
        self.filament_reply: tuple[int, dict, dict] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v1")
        if path == "/oauth/device/code":
            return httpx.Response(
                200,
                json={
                    "device_code": "DEVICE-SECRET",
                    "user_code": "WDJB-MJHT",
                    "verification_uri": "https://3dfilamentprofiles.com/connect",
                    "verification_uri_complete": "https://3dfilamentprofiles.com/connect?code=WDJB-MJHT",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if path == "/oauth/token":
            status, body = self.token_replies.pop(0)
            return httpx.Response(status, json=body)
        if path == "/oauth/revoke":
            return httpx.Response(200, json={})
        if path == "/me":
            return httpx.Response(self.me_reply[0], json=self.me_reply[1])
        if path == "/filaments" and request.url.params.get("gtin"):
            return httpx.Response(200, json={"data": [SUMMARY, {**SUMMARY, "id": 102, "color": "Black"}]})
        if path.startswith("/filaments/"):
            if self.filament_reply:
                status, body, headers = self.filament_reply
                return httpx.Response(status, json=body, headers=headers)
            if path == "/filaments/101":
                return httpx.Response(200, json=FULL_RECORD)
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "No such filament"}})
        return httpx.Response(500)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == f"/api/v1{path}"]


@pytest.fixture
def fake():
    fake = Fake3dfp()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    service.set_shared_http_client(client)
    service.clear_filament_cache()
    yield fake
    service.set_shared_http_client(None)
    service.clear_filament_cache()


async def _connect(client: AsyncClient, fake: Fake3dfp, headers: dict | None = None) -> None:
    assert (await client.post("/api/v1/threedfp/device/start", headers=headers)).status_code == 200
    fake.token_replies.append((200, {"access_token": "3dfpat_TOKEN", "token_type": "Bearer"}))
    resp = await client.post("/api/v1/threedfp/device/poll", headers=headers)
    assert resp.json() == {"status": "complete", "connected": True}


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content.decode()))


class TestParseLookup:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("4019238055001", ("gtin", "4019238055001")),
            (" 40192380 ", ("gtin", "40192380")),
            ("4019-2380-5500", ("gtin", "401923805500")),
            ("101", ("id", "101")),
            ("0101", ("id", "101")),
            ("https://3dfilamentprofiles.com/filament/details/4711?x=1", ("id", "4711")),
            ("0", None),
            ("123456789012345", None),
            ("PLA Basic", None),
        ],
    )
    def test_barcode_id_or_link(self, query, expected):
        assert parse_lookup(query) == expected


class TestConnecting:
    @pytest.mark.asyncio
    async def test_start_keeps_the_device_code_on_the_server(self, async_client: AsyncClient, fake, db_session):
        resp = await async_client.post("/api/v1/threedfp/device/start")

        assert resp.status_code == 200
        body = resp.json()
        assert body["user_code"] == "WDJB-MJHT"
        assert body["verification_uri_complete"].endswith("code=WDJB-MJHT")
        assert "DEVICE-SECRET" not in resp.text
        request = fake.calls("/oauth/device/code")[0]
        form = _form(request)
        assert form["client_id"] == "bambuddy"
        assert form["scope"] == "filaments:read spools:read spools:write"
        assert form["device_name"].startswith("Bambuddy on ")
        assert request.headers["User-Agent"] == f"Bambuddy/{APP_VERSION}"
        # Auth is off: the pending code is in the settings table, under a key a
        # Git backup writes as a placeholder.
        row = (
            await db_session.execute(select(Settings).where(Settings.key == "threedfp_pending_device_secret"))
        ).scalar_one()
        assert row.value == "DEVICE-SECRET"

    @pytest.mark.asyncio
    async def test_poll_reports_pending_then_stores_the_token(self, async_client: AsyncClient, fake, db_session):
        await async_client.post("/api/v1/threedfp/device/start")
        fake.token_replies += [
            (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down"}),
            (200, {"access_token": "3dfpat_TOKEN"}),
        ]

        assert (await async_client.post("/api/v1/threedfp/device/poll")).json()["status"] == "authorization_pending"
        assert (await async_client.post("/api/v1/threedfp/device/poll")).json()["status"] == "slow_down"
        done = await async_client.post("/api/v1/threedfp/device/poll")

        assert done.json() == {"status": "complete", "connected": True}
        assert _form(fake.calls("/oauth/token")[0])["device_code"] == "DEVICE-SECRET"
        status = (await async_client.get("/api/v1/threedfp/status")).json()
        assert status == {"connected": True, "email": "maker@example.com"}
        # The pairing is over: polling again has nothing to poll.
        assert (await async_client.post("/api/v1/threedfp/device/poll")).status_code == 400
        assert "3dfpat_TOKEN" not in (await async_client.get("/api/v1/threedfp/status")).text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", ["access_denied", "expired_token"])
    async def test_a_final_answer_ends_the_attempt(self, async_client: AsyncClient, fake, error):
        await async_client.post("/api/v1/threedfp/device/start")
        fake.token_replies.append((400, {"error": error}))

        assert (await async_client.post("/api/v1/threedfp/device/poll")).json() == {
            "status": error,
            "connected": False,
        }
        assert (await async_client.post("/api/v1/threedfp/device/poll")).status_code == 400
        assert (await async_client.get("/api/v1/threedfp/status")).json()["connected"] is False

    @pytest.mark.asyncio
    async def test_an_unexpected_token_reply_keeps_the_attempt(self, async_client: AsyncClient, fake):
        """A 500 is not the user's answer: the next poll may still succeed."""
        await async_client.post("/api/v1/threedfp/device/start")
        fake.token_replies += [(500, {}), (200, {"access_token": "3dfpat_TOKEN"})]

        assert (await async_client.post("/api/v1/threedfp/device/poll")).status_code == 502
        assert (await async_client.post("/api/v1/threedfp/device/poll")).json()["connected"] is True

    @pytest.mark.asyncio
    async def test_an_old_attempt_expires_without_asking_3dfp(self, async_client: AsyncClient, fake, db_session):
        await async_client.post("/api/v1/threedfp/device/start")
        row = (await db_session.execute(select(Settings).where(Settings.key == "threedfp_pending_at"))).scalar_one()
        row.value = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        await db_session.commit()

        resp = await async_client.post("/api/v1/threedfp/device/poll")

        assert resp.json()["status"] == "expired_token"
        assert fake.calls("/oauth/token") == []

    @pytest.mark.asyncio
    async def test_disconnect_revokes_and_forgets_the_token(self, async_client: AsyncClient, fake, db_session):
        await _connect(async_client, fake)

        assert (await async_client.post("/api/v1/threedfp/disconnect")).status_code == 200

        assert _form(fake.calls("/oauth/revoke")[0]) == {"token": "3dfpat_TOKEN", "client_id": "bambuddy"}
        assert (await async_client.get("/api/v1/threedfp/status")).json()["connected"] is False
        keys = (await db_session.execute(select(Settings.key).where(Settings.key.like("threedfp_%")))).scalars()
        assert list(keys) == []


class TestAccount:
    @pytest.mark.asyncio
    async def test_reports_plan_scopes_and_quota(self, async_client: AsyncClient, fake):
        await _connect(async_client, fake)

        body = (await async_client.get("/api/v1/threedfp/account")).json()

        assert body["plan"] == "free"
        assert body["active_scopes"] == ["filaments:read"]
        assert body["quota"]["remaining"] == 97
        assert body["upgrade_url"] == "https://3dfilamentprofiles.com/my/api"
        assert fake.calls("/me")[-1].headers["Authorization"] == "Bearer 3dfpat_TOKEN"

    @pytest.mark.asyncio
    async def test_a_disconnected_token_is_forgotten_and_answered_409(self, async_client: AsyncClient, fake):
        """Not 401: the frontend signs the user out of Bambuddy on some 401s,
        and 3DFP rejecting its own token says nothing about that session."""
        await _connect(async_client, fake)
        fake.me_reply = (401, {"error": {"code": "invalid_key", "message": "Invalid API key"}})

        resp = await async_client.get("/api/v1/threedfp/account")

        assert resp.status_code == 409
        assert "Invalid API key" not in resp.text
        assert (await async_client.get("/api/v1/threedfp/status")).json()["connected"] is False

    @pytest.mark.asyncio
    async def test_any_other_401_keeps_the_connection(self, async_client: AsyncClient, fake):
        """Only 3DFP's own invalid_key means the token is gone (#2562)."""
        await _connect(async_client, fake)
        fake.me_reply = (401, {})

        resp = await async_client.get("/api/v1/threedfp/account")

        assert resp.status_code == 502
        assert (await async_client.get("/api/v1/threedfp/status")).json()["connected"] is True

    @pytest.mark.asyncio
    async def test_not_connected_is_409_without_a_call(self, async_client: AsyncClient, fake):
        assert (await async_client.get("/api/v1/threedfp/account")).status_code == 409
        assert (await async_client.get("/api/v1/threedfp/lookup", params={"q": "101"})).status_code == 409
        assert fake.requests == []


class TestLookup:
    @pytest.mark.asyncio
    async def test_a_barcode_returns_every_match(self, async_client: AsyncClient, fake):
        await _connect(async_client, fake)

        resp = await async_client.get("/api/v1/threedfp/lookup", params={"q": "4019238055001"})

        assert [f["id"] for f in resp.json()["results"]] == [101, 102]
        assert fake.calls("/filaments")[0].url.params["gtin"] == "4019238055001"

    @pytest.mark.asyncio
    async def test_an_id_returns_the_full_record_cleaned(self, async_client: AsyncClient, fake):
        await _connect(async_client, fake)

        [filament] = (await async_client.get("/api/v1/threedfp/lookup", params={"q": "101"})).json()["results"]

        assert filament["bambu_filament_id"] == "GFA00"
        assert filament["rgb"] == "#FFFFFF"
        assert filament["nominal_weight"] == 1000
        assert filament["spool_weight"] == 250
        # The store url ends up in an href: only https links are kept.
        assert [s["url"] for s in filament["stores"]] == ["https://3dfp.to/p/35"]

    @pytest.mark.asyncio
    async def test_odd_field_types_are_dropped_not_fatal(self, async_client: AsyncClient, fake):
        await _connect(async_client, fake)
        fake.filament_reply = (
            200,
            {**FULL_RECORD, "rgb": "white", "nominal_weight": "1kg", "brand_name": 5, "stores": "none"},
            {},
        )

        resp = await async_client.get("/api/v1/threedfp/filaments/101")

        assert resp.status_code == 200
        body = resp.json()
        assert (body["rgb"], body["nominal_weight"], body["brand_name"], body["stores"]) == (None, None, None, [])

    @pytest.mark.asyncio
    async def test_an_unknown_id_finds_nothing(self, async_client: AsyncClient, fake):
        await _connect(async_client, fake)

        resp = await async_client.get("/api/v1/threedfp/lookup", params={"q": "999"})

        assert resp.json() == {"results": []}

    @pytest.mark.asyncio
    async def test_text_is_refused_without_a_call(self, async_client: AsyncClient, fake):
        await _connect(async_client, fake)
        before = len(fake.requests)

        assert (await async_client.get("/api/v1/threedfp/lookup", params={"q": "PLA"})).status_code == 400
        assert len(fake.requests) == before

    @pytest.mark.asyncio
    async def test_a_filament_is_fetched_once_a_day(self, async_client: AsyncClient, fake):
        """A free account has 100 requests a month; a merged id is cached
        under the id asked for and the one returned."""
        await _connect(async_client, fake)
        fake.filament_reply = (200, {**FULL_RECORD, "id": 202}, {})

        first = await async_client.get("/api/v1/threedfp/filaments/101")
        again = await async_client.get("/api/v1/threedfp/filaments/101")
        merged = await async_client.get("/api/v1/threedfp/filaments/202")

        assert first.json()["id"] == again.json()["id"] == merged.json()["id"] == 202
        assert len([r for r in fake.requests if r.url.path.startswith("/api/v1/filaments/")]) == 1

    @pytest.mark.asyncio
    async def test_plan_and_rate_limits_are_passed_on(self, async_client: AsyncClient, fake):
        await _connect(async_client, fake)
        fake.filament_reply = (402, {"error": {"code": "no_access", "message": "no"}}, {})
        assert (await async_client.get("/api/v1/threedfp/filaments/101")).status_code == 402

        fake.filament_reply = (429, {"error": {"code": "rate_limited", "message": "slow"}}, {"Retry-After": "30"})
        resp = await async_client.get("/api/v1/threedfp/filaments/101")
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "30"
        assert (await async_client.get("/api/v1/threedfp/status")).json()["connected"] is True


class TestPerUser:
    @pytest.fixture
    async def users(self, async_client: AsyncClient):
        await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True, "admin_username": "dfpadmin", "admin_password": "AdminPass1!"},
        )
        admin = (
            await async_client.post("/api/v1/auth/login", json={"username": "dfpadmin", "password": "AdminPass1!"})
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin}"}
        groups = {g["name"]: g["id"] for g in (await async_client.get("/api/v1/groups/", headers=admin_headers)).json()}
        tokens = {}
        for name, group in (("op1", "Operators"), ("op2", "Operators"), ("viewer", "Viewers")):
            await async_client.post(
                "/api/v1/users/",
                headers=admin_headers,
                json={"username": name, "password": "Userpass1!", "group_ids": [groups[group]]},
            )
            login = await async_client.post("/api/v1/auth/login", json={"username": name, "password": "Userpass1!"})
            tokens[name] = {"Authorization": f"Bearer {login.json()['access_token']}"}
        return tokens

    @pytest.mark.asyncio
    async def test_each_user_has_their_own_connection(self, async_client: AsyncClient, fake, users, db_session):
        await _connect(async_client, fake, users["op1"])

        assert (await async_client.get("/api/v1/threedfp/status", headers=users["op1"])).json()["connected"] is True
        assert (await async_client.get("/api/v1/threedfp/status", headers=users["op2"])).json()["connected"] is False
        op1 = (await db_session.execute(select(User).where(User.username == "op1"))).scalar_one()
        await db_session.refresh(op1)
        assert op1.threedfp_token == "3dfpat_TOKEN"
        assert op1.threedfp_pending_device_code is None
        settings = (await db_session.execute(select(Settings.key).where(Settings.key.like("threedfp_%")))).scalars()
        assert list(settings) == []

    @pytest.mark.asyncio
    async def test_viewers_cannot_connect(self, async_client: AsyncClient, fake, users):
        assert (await async_client.post("/api/v1/threedfp/device/start", headers=users["viewer"])).status_code == 403
        assert fake.requests == []


def test_operators_get_the_permission_and_viewers_do_not():
    assert Permission.THREEDFP_AUTH.value in DEFAULT_GROUPS["Operators"]["permissions"]
    assert Permission.THREEDFP_AUTH.value not in DEFAULT_GROUPS["Viewers"]["permissions"]


SPOOLMAN_SPOOL = {
    "id": 7,
    "filament": {"id": 1, "name": "PLA", "material": "PLA", "weight": 1000, "color_hex": "FFFFFF", "vendor": None},
    "remaining_weight": 600.0,
    "used_weight": 400.0,
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "extra": {},
}


class TestSpoolLinks:
    @pytest.fixture
    async def spool(self, db_session: AsyncSession) -> Spool:
        spool = Spool(material="PLA", label_weight=1000, core_weight=250, weight_used=0, rgba="FFFFFFFF")
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    @pytest.fixture
    def spoolman(self, db_session: AsyncSession):
        client = MagicMock()
        client.base_url = "http://localhost:7912"
        client.get_spool = AsyncMock(return_value=SPOOLMAN_SPOOL)
        client.delete_spool = AsyncMock(return_value=None)
        with patch("backend.app.api.routes.spoolman_inventory._get_client", AsyncMock(return_value=client)):
            yield client

    @pytest.mark.asyncio
    async def test_link_change_and_unlink_a_spool(self, async_client: AsyncClient, spool):
        url = f"/api/v1/threedfp/spool-links/inventory/{spool.id}"
        assert (await async_client.get(url)).json() == {"filament_id": None}

        assert (await async_client.put(url, json={"filament_id": 101})).status_code == 200
        assert (await async_client.put(url, json={"filament_id": 202})).status_code == 200
        assert (await async_client.get(url)).json() == {"filament_id": 202}

        assert (await async_client.put(url, json={"filament_id": None})).status_code == 200
        assert (await async_client.get(url)).json() == {"filament_id": None}

    @pytest.mark.asyncio
    async def test_unknown_spool_and_bad_id_are_refused(self, async_client: AsyncClient, spool):
        assert (
            await async_client.put("/api/v1/threedfp/spool-links/inventory/9999", json={"filament_id": 1})
        ).status_code == 404
        url = f"/api/v1/threedfp/spool-links/inventory/{spool.id}"
        assert (await async_client.put(url, json={"filament_id": 0})).status_code == 422
        assert (await async_client.put("/api/v1/threedfp/spool-links/other/1", json={})).status_code == 422

    @pytest.mark.asyncio
    async def test_deleting_a_spool_deletes_its_link(self, async_client: AsyncClient, spool, db_session):
        """SQLite doesn't enforce the cascade: without the rule, the next spool
        given the id would show this one's filament and Reorder link."""
        await async_client.put(f"/api/v1/threedfp/spool-links/inventory/{spool.id}", json={"filament_id": 101})

        assert (await async_client.delete(f"/api/v1/inventory/spools/{spool.id}")).status_code == 200

        assert (await db_session.execute(select(ThreeDfpSpoolLink))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_a_spoolman_spool_is_checked_and_its_link_dropped_with_it(
        self, async_client: AsyncClient, spoolman, db_session
    ):
        url = "/api/v1/threedfp/spool-links/spoolman/7"
        assert (await async_client.put(url, json={"filament_id": 101})).status_code == 200
        spoolman.get_spool.assert_awaited_with(7)
        link = (await db_session.execute(select(ThreeDfpSpoolLink))).scalar_one()
        assert (link.spool_id, link.spoolman_spool_id, link.filament_id) == (None, 7, 101)

        with patch("backend.app.api.routes.spoolman_inventory._release_spool_slots", AsyncMock(return_value=[])):
            assert (await async_client.delete("/api/v1/spoolman/inventory/spools/7")).status_code == 200

        db_session.expire_all()
        assert (await db_session.execute(select(ThreeDfpSpoolLink))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_the_same_number_in_both_inventories_is_two_links(self, async_client: AsyncClient, spool, spoolman):
        await async_client.put(f"/api/v1/threedfp/spool-links/inventory/{spool.id}", json={"filament_id": 1})
        await async_client.put(f"/api/v1/threedfp/spool-links/spoolman/{spool.id}", json={"filament_id": 2})

        assert (await async_client.get(f"/api/v1/threedfp/spool-links/inventory/{spool.id}")).json()["filament_id"] == 1
        assert (await async_client.get(f"/api/v1/threedfp/spool-links/spoolman/{spool.id}")).json()["filament_id"] == 2
