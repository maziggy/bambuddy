"""The Manyfold client (#1471), against a fake Manyfold install."""

from __future__ import annotations

import httpx
import pytest

from backend.app.services.model_providers.manyfold import service as svc
from backend.app.services.model_providers.manyfold.config import ManyfoldConfig, normalize_url
from backend.app.services.model_providers.manyfold.service import (
    ManyfoldAuthError,
    ManyfoldNotFoundError,
    ManyfoldService,
    ManyfoldUnavailableError,
)
from backend.tests._fixtures.manyfold import BASE, PNG, STL, THREE_MF, FakeFile, FakeManyfold

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def fresh_tokens():
    svc.clear_token_cache()
    yield
    svc.clear_token_cache()


@pytest.fixture
def manyfold() -> FakeManyfold:
    fake = FakeManyfold()
    fake.add_model(
        "cube01",
        "Calibration Cube",
        {
            "f1": FakeFile("cube.stl", "model/stl", STL, render=PNG),
            "f2": FakeFile("cube.3mf", "model/3mf", THREE_MF),
            "f3": FakeFile("photo.jpg", "image/jpeg", b"\xff\xd8\xff" + b"\x00" * 40),
            "f4": FakeFile("notes.pdf", "application/pdf", b"%PDF-1.7"),
        },
        preview="f1",
    )
    fake.add_model("boat02", "Benchy", {"f9": FakeFile("benchy.stl", "model/stl", STL)})
    fake.add_model("vase03", "Spiral Vase", {})
    return fake


def _service(fake: FakeManyfold, **overrides) -> ManyfoldService:
    config = ManyfoldConfig(
        url=overrides.get("url", BASE),
        client_id=overrides.get("client_id", fake.client_id),
        client_secret=overrides.get("client_secret", fake.client_secret),
    )
    return ManyfoldService(config, client=fake.client())


class TestSignIn:
    @pytest.mark.asyncio
    async def test_one_token_serves_many_requests(self, manyfold):
        # Manyfold allows 10 sign-ins in 3 minutes; a page of previews must not use them up.
        service = _service(manyfold)
        for _ in range(5):
            await service.list_models()
        await _service(manyfold).get_model("cube01")
        assert manyfold.token_requests == 1

    @pytest.mark.asyncio
    async def test_asks_only_for_read_access(self, manyfold):
        await _service(manyfold).list_models()
        token_request = next(r for r in manyfold.requests if r.url.path == "/oauth/token")
        assert "scope=public+read" in token_request.content.decode()
        assert "grant_type=client_credentials" in token_request.content.decode()

    @pytest.mark.asyncio
    async def test_a_refused_token_is_renewed_once(self, manyfold):
        service = _service(manyfold)
        await service.list_models()
        manyfold.revoke_tokens()  # e.g. Manyfold restarted, or the token was revoked
        result = await service.list_models()
        assert result["total"] == 3
        assert manyfold.token_requests == 2

    @pytest.mark.asyncio
    async def test_an_expired_token_is_renewed_before_use(self, manyfold):
        manyfold.token_lifetime = 60  # shorter than the renewal margin
        service = _service(manyfold)
        await service.list_models()
        await service.list_models()
        assert manyfold.token_requests == 2

    @pytest.mark.asyncio
    async def test_wrong_secret(self, manyfold):
        with pytest.raises(ManyfoldAuthError) as err:
            await _service(manyfold, client_secret="nope").list_models()
        assert err.value.code == "manyfold_credentials"

    @pytest.mark.asyncio
    async def test_a_refused_secret_is_not_tried_again_at_once(self, manyfold):
        # A page or a bulk import must not use up Manyfold's 10 sign-ins in 3 minutes.
        for _ in range(5):
            with pytest.raises(ManyfoldAuthError) as err:
                await _service(manyfold, client_secret="nope").list_models()
            assert err.value.code == "manyfold_credentials"
        assert manyfold.token_requests == 1
        # The right secret is a different sign-in and goes through at once.
        assert (await _service(manyfold).list_models())["total"] == 3
        assert manyfold.token_requests == 2

    @pytest.mark.asyncio
    async def test_saving_the_connection_forgets_a_refusal(self, manyfold):
        with pytest.raises(ManyfoldAuthError):
            await _service(manyfold, client_secret="nope").list_models()
        svc.clear_token_cache()
        with pytest.raises(ManyfoldAuthError):
            await _service(manyfold, client_secret="nope").list_models()
        assert manyfold.token_requests == 2

    @pytest.mark.asyncio
    async def test_a_401_from_object_storage_does_not_renew_the_token(self, manyfold):
        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.host == "storage.example.com":
                return httpx.Response(401)
            if request.url.path == "/models/cube01/raw/cube.stl":
                return httpx.Response(302, headers={"Location": "https://storage.example.com/cube.stl"})
            return manyfold.handle(request)

        service = ManyfoldService(
            ManyfoldConfig(BASE, manyfold.client_id, manyfold.client_secret),
            client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        )
        file = await service.get_file("cube01", "f1")
        with pytest.raises(ManyfoldAuthError):
            await service.download_file(file)
        assert manyfold.token_requests == 1

    @pytest.mark.asyncio
    async def test_application_without_read_scope(self, manyfold):
        manyfold.scopes = "public upload"
        with pytest.raises(ManyfoldAuthError) as err:
            await _service(manyfold).list_models()
        assert err.value.code == "manyfold_scope"

    @pytest.mark.asyncio
    async def test_rate_limited_sign_in(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(429))
        service = ManyfoldService(ManyfoldConfig(BASE, "a", "b"), client=httpx.AsyncClient(transport=transport))
        with pytest.raises(ManyfoldUnavailableError) as err:
            await service.list_models()
        assert err.value.code == "manyfold_rate_limited"

    @pytest.mark.asyncio
    async def test_unreachable(self):
        def refuse(request):
            raise httpx.ConnectError("connection refused")

        service = ManyfoldService(
            ManyfoldConfig(BASE, "a", "b"), client=httpx.AsyncClient(transport=httpx.MockTransport(refuse))
        )
        with pytest.raises(ManyfoldUnavailableError) as err:
            await service.list_models()
        assert err.value.code == "manyfold_unreachable"

    @pytest.mark.asyncio
    async def test_not_configured(self, manyfold):
        with pytest.raises(ManyfoldAuthError) as err:
            await _service(manyfold, client_secret="").list_models()
        assert err.value.code == "manyfold_not_configured"
        assert manyfold.requests == []

    @pytest.mark.asyncio
    async def test_changed_credentials_get_their_own_token(self, manyfold):
        await _service(manyfold).list_models()
        other = FakeManyfold(client_id="other-id", client_secret="other-secret")
        other.models = manyfold.models
        await _service(other).list_models()
        assert other.token_requests == 1


class TestBrowsing:
    @pytest.mark.asyncio
    async def test_list_pages_and_search(self, manyfold):
        service = _service(manyfold)
        first = await service.list_models()
        assert first == {
            "total": 3,
            "page": 1,
            "has_next": True,
            "has_previous": False,
            "models": [{"id": "cube01", "name": "Calibration Cube"}, {"id": "boat02", "name": "Benchy"}],
        }
        second = await service.list_models(page=2)
        assert second["models"] == [{"id": "vase03", "name": "Spiral Vase"}]
        assert second["has_previous"] and not second["has_next"]
        found = await service.list_models(query="  bench ")
        assert [m["id"] for m in found["models"]] == ["boat02"]
        assert manyfold.requests[-1].url.params["q"] == "bench"

    @pytest.mark.asyncio
    async def test_model_details(self, manyfold):
        model = await _service(manyfold).get_model("cube01")
        assert model["name"] == "Calibration Cube"
        assert model["license"] == "CC-BY-4.0"
        assert model["tags"] == ["test", "cube"]
        assert model["url"] == f"{BASE}/models/cube01"
        assert model["preview_file_id"] == "f1"
        assert [(f["id"], f["importable"]) for f in model["files"]] == [
            ("f1", True),
            ("f2", True),
            ("f3", False),
            ("f4", False),
        ]

    @pytest.mark.asyncio
    async def test_missing_model(self, manyfold):
        with pytest.raises(ManyfoldNotFoundError):
            await _service(manyfold).get_model("gone99")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["../etc", "a/b", "", "x" * 65, "a b", "a?b=1"])
    async def test_malformed_ids_never_reach_manyfold(self, manyfold, bad):
        with pytest.raises(ManyfoldNotFoundError):
            await _service(manyfold).get_model(bad)
        assert manyfold.requests == []

    @pytest.mark.asyncio
    async def test_sub_path_installs(self, manyfold):
        # Behind a reverse proxy at /manyfold: every request keeps the prefix.
        seen = []

        def strip_prefix(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            stripped = request.url.copy_with(raw_path=request.url.raw_path.replace(b"/manyfold", b"", 1))
            return manyfold.handle(
                httpx.Request(request.method, stripped, headers=request.headers, content=request.content)
            )

        service = ManyfoldService(
            ManyfoldConfig(f"{BASE}/manyfold", manyfold.client_id, manyfold.client_secret),
            client=httpx.AsyncClient(transport=httpx.MockTransport(strip_prefix)),
        )
        await service.get_model("cube01")
        assert seen and all(path.startswith("/manyfold/") for path in seen)


class TestFiles:
    @pytest.mark.asyncio
    async def test_download_uses_the_raw_link(self, manyfold):
        service = _service(manyfold)
        file = await service.get_file("cube01", "f1")
        assert file["filename"] == "cube.stl"
        assert file["raw_path"] == "/models/cube01/raw/cube.stl"
        assert await service.download_file(file) == STL

    @pytest.mark.asyncio
    async def test_names_with_spaces_and_folders(self, manyfold):
        manyfold.add_model("parts04", "Parts", {"p1": FakeFile("sub dir/My Part.stl", "model/stl", STL)})
        service = _service(manyfold)
        file = await service.get_file("parts04", "p1")
        assert file["filename"] == "My Part.stl"
        assert await service.download_file(file) == STL

    @pytest.mark.asyncio
    async def test_a_download_link_pointing_elsewhere_is_not_followed(self, manyfold):
        # contentUrl is only read for the path under /models/<id>/raw/.
        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/models/cube01/model_files/f1":
                return httpx.Response(200, json={"contentUrl": "http://evil.test/models/other/raw/x.stl", "name": "x"})
            return manyfold.handle(request)

        service = ManyfoldService(
            ManyfoldConfig(BASE, manyfold.client_id, manyfold.client_secret),
            client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        )
        with pytest.raises(ManyfoldUnavailableError):
            await service.get_file("cube01", "f1")

    @pytest.mark.asyncio
    async def test_dot_dot_in_the_link_is_refused(self, manyfold):
        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/models/cube01/model_files/f1":
                return httpx.Response(200, json={"contentUrl": f"{BASE}/models/cube01/raw/..%2F..%2Fsecrets"})
            return manyfold.handle(request)

        service = ManyfoldService(
            ManyfoldConfig(BASE, manyfold.client_id, manyfold.client_secret),
            client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        )
        with pytest.raises(ManyfoldUnavailableError):
            await service.get_file("cube01", "f1")

    @pytest.mark.asyncio
    async def test_object_storage_redirect_is_followed_without_the_token(self, manyfold):
        # Manyfold on S3-style storage answers a download with a redirect.
        seen: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.host == "storage.example.com":
                return httpx.Response(200, content=STL)
            if request.url.path == "/models/cube01/raw/cube.stl":
                return httpx.Response(302, headers={"Location": "https://storage.example.com/bucket/cube.stl?sig=x"})
            return manyfold.handle(request)

        service = ManyfoldService(
            ManyfoldConfig(BASE, manyfold.client_id, manyfold.client_secret),
            client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        )
        file = await service.get_file("cube01", "f1")
        assert await service.download_file(file) == STL
        storage = [r for r in seen if r.url.host == "storage.example.com"]
        assert storage and "Authorization" not in storage[0].headers

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "target", ["http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/", "file:///etc/passwd"]
    )
    async def test_a_redirect_to_a_dangerous_target_is_refused(self, manyfold, target):
        seen: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/models/cube01/raw/cube.stl":
                return httpx.Response(302, headers={"Location": target})
            return manyfold.handle(request)

        service = ManyfoldService(
            ManyfoldConfig(BASE, manyfold.client_id, manyfold.client_secret),
            client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        )
        file = await service.get_file("cube01", "f1")
        with pytest.raises(ManyfoldUnavailableError):
            await service.download_file(file)
        assert all(r.url.host == "manyfold.test" for r in seen)

    @pytest.mark.asyncio
    async def test_endless_redirects_stop(self, manyfold):
        def answer(request: httpx.Request) -> httpx.Response:
            if "/raw/" in request.url.path:
                return httpx.Response(302, headers={"Location": f"{BASE}/models/cube01/raw/cube.stl"})
            return manyfold.handle(request)

        service = ManyfoldService(
            ManyfoldConfig(BASE, manyfold.client_id, manyfold.client_secret),
            client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        )
        file = await service.get_file("cube01", "f1")
        with pytest.raises(ManyfoldUnavailableError):
            await service.download_file(file)

    @pytest.mark.asyncio
    async def test_oversized_file_is_refused(self, manyfold, monkeypatch):
        monkeypatch.setattr(svc, "MAX_FILE_BYTES", 50)
        service = _service(manyfold)
        file = await service.get_file("cube01", "f1")
        with pytest.raises(ManyfoldUnavailableError) as err:
            await service.download_file(file)
        assert err.value.code == "manyfold_too_large"


class TestPreviews:
    @pytest.mark.asyncio
    async def test_rendered_preview_of_a_3d_file(self, manyfold):
        data, content_type = await _service(manyfold).fetch_preview("cube01")
        assert (data, content_type) == (PNG, "image/png")
        preview_request = manyfold.requests[-1]
        assert preview_request.url.path == "/models/cube01/model_files/f1.stl"
        assert preview_request.url.params["derivative"] == "render"

    @pytest.mark.asyncio
    async def test_image_preview_asks_for_the_small_copy(self, manyfold):
        manyfold.models["cube01"]["preview"] = "f3"
        data, content_type = await _service(manyfold).fetch_preview("cube01")
        assert content_type == "image/jpeg"
        assert manyfold.requests[-1].url.params["derivative"] == "preview"

    @pytest.mark.asyncio
    async def test_no_render_means_no_preview_not_the_whole_stl(self, manyfold):
        # Without a render Manyfold sends the original file: refuse it, don't pass an STL off as an image.
        manyfold.models["cube01"]["files"]["f1"].render = None
        with pytest.raises(ManyfoldNotFoundError):
            await _service(manyfold).fetch_preview("cube01")

    @pytest.mark.asyncio
    async def test_model_without_preview(self, manyfold):
        with pytest.raises(ManyfoldNotFoundError):
            await _service(manyfold).fetch_preview("boat02")


class TestUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("http://docker:3214", "http://docker:3214"),
            ("  https://models.example.com/ ", "https://models.example.com"),
            ("https://example.com/manyfold/", "https://example.com/manyfold"),
        ],
    )
    def test_accepted(self, raw, expected):
        assert normalize_url(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "docker:3214",
            "ftp://docker",
            "http://",
            "https://user:pw@host",
            "http://host/?a=1",
            "http://host/#x",
            "",
            # The LAN-service tier: never a Manyfold, under any topology.
            "http://169.254.169.254/",
            "http://metadata.google.internal/",
            "http://2130706433/",
        ],
    )
    def test_refused(self, raw):
        with pytest.raises(ValueError):
            normalize_url(raw)
