"""A fake Manyfold install for the Manyfold tests (#1471).

Answers the requests Bambuddy makes the way Manyfold's v0 API does (read from
Manyfold's source: the doorkeeper token endpoint, the JSON-LD serializers,
``model_files#show`` with a derivative, ``model_files#raw``), through an
``httpx.MockTransport``, and records what it was asked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote

import httpx

BASE = "http://manyfold.test:3214"
API = "application/vnd.manyfold.v0+json"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
STL = b"solid cube\n" + b"facet normal 0 0 1\n" * 40 + b"endsolid cube\n"
THREE_MF = b"PK\x03\x04" + b"\x00" * 64


@dataclass
class FakeFile:
    filename: str
    mime: str
    data: bytes
    name: str = ""
    render: bytes | None = None


@dataclass
class FakeManyfold:
    client_id: str = "app-id"
    client_secret: str = "app-secret"
    scopes: str = "public read"
    page_size: int = 2
    models: dict[str, dict] = field(default_factory=dict)
    token_requests: int = 0
    requests: list[httpx.Request] = field(default_factory=list)
    valid_tokens: set[str] = field(default_factory=set)
    token_lifetime: int = 7200

    def add_model(self, model_id: str, name: str, files: dict[str, FakeFile], preview: str | None = None) -> None:
        self.models[model_id] = {"name": name, "files": files, "preview": preview}

    def revoke_tokens(self) -> None:
        self.valid_tokens.clear()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())

    # ---- handler ----

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = unquote(request.url.raw_path.decode().split("?")[0])
        if request.method == "POST" and path == "/oauth/token":
            return self._token(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.valid_tokens:
            return httpx.Response(401, json={"error": "unauthorized"})
        parts = [p for p in path.split("/") if p]
        api = request.headers.get("Accept") == API
        if parts == ["models"] and api:
            return self._list(request)
        if len(parts) == 2 and parts[0] == "models" and api:
            return self._model(parts[1])
        if len(parts) >= 4 and parts[0] == "models" and parts[2] == "raw":
            return self._raw(parts[1], "/".join(parts[3:]))
        if len(parts) == 4 and parts[0] == "models" and parts[2] == "model_files":
            return self._file(parts[1], parts[3], api, request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_requests += 1
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("client_id") != self.client_id or form.get("client_secret") != self.client_secret:
            return httpx.Response(401, json={"error": "invalid_client"})
        wanted = set(form.get("scope", "").split())
        if not wanted <= set(self.scopes.split()):
            return httpx.Response(400, json={"error": "invalid_scope"})
        token = f"token-{self.token_requests}"
        self.valid_tokens.add(token)
        return httpx.Response(
            200,
            json={
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": self.token_lifetime,
                "scope": " ".join(sorted(wanted)),
            },
        )

    def _list(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        page = int(request.url.params.get("page", "1"))
        ids = [mid for mid, m in self.models.items() if q.lower() in m["name"].lower()]
        start = (page - 1) * self.page_size
        chunk = ids[start : start + self.page_size]
        pages = max(1, -(-len(ids) // self.page_size))
        view = {"@id": f"/models?page={page}", "first": "/models?page=1", "last": f"/models?page={pages}"}
        if page > 1:
            view["previous"] = f"/models?page={page - 1}"
        if page < pages:
            view["next"] = f"/models?page={page + 1}"
        return httpx.Response(
            200,
            headers={"Content-Type": API},
            content=json.dumps(
                {
                    "@id": "/models",
                    "@type": "hydra:Collection",
                    "totalItems": len(ids),
                    "member": [
                        {"@id": f"{BASE}/models/{mid}", "@type": "3DModel", "name": self.models[mid]["name"]}
                        for mid in chunk
                    ],
                    "view": view,
                }
            ),
        )

    def _model(self, model_id: str) -> httpx.Response:
        model = self.models.get(model_id)
        if model is None:
            return httpx.Response(404)
        body = {
            "@id": f"{BASE}/models/{model_id}",
            "@type": "3DModel",
            "name": model["name"],
            "description": "A test model",
            "spdx:license": {"@type": "spdx:License", "licenseId": "CC-BY-4.0"},
            "keywords": ["test", "cube"],
            "hasPart": [
                {
                    "@id": f"{BASE}/models/{model_id}/model_files/{fid}",
                    "@type": "3DModel",
                    "name": f.name or fid,
                    "encodingFormat": f.mime,
                }
                for fid, f in model["files"].items()
            ],
        }
        if model["preview"]:
            body["preview_file"] = {
                "@id": f"{BASE}/models/{model_id}/model_files/{model['preview']}",
                "@type": "3DModel",
            }
        return httpx.Response(200, headers={"Content-Type": API}, content=json.dumps(body))

    def _file(self, model_id: str, file_part: str, api: bool, request: httpx.Request) -> httpx.Response:
        file_id = file_part.split(".", 1)[0]
        f = self.models.get(model_id, {}).get("files", {}).get(file_id)
        if f is None:
            return httpx.Response(404)
        if api:
            return httpx.Response(
                200,
                headers={"Content-Type": API},
                content=json.dumps(
                    {
                        "@id": f"{BASE}/models/{model_id}/model_files/{file_id}",
                        "name": f.name or file_id,
                        "contentUrl": f"{BASE}/models/{model_id}/raw/{quote(f.filename)}",
                        "encodingFormat": f.mime,
                        "contentSize": len(f.data),
                    }
                ),
            )
        derivative = request.url.params.get("derivative")
        if derivative and f.render is not None:
            return httpx.Response(200, content=f.render)
        # Like Manyfold: no derivative means the original file.
        return httpx.Response(200, content=f.data)

    def _raw(self, model_id: str, filename: str) -> httpx.Response:
        for f in self.models.get(model_id, {}).get("files", {}).values():
            if f.filename == filename:
                return httpx.Response(200, content=f.data)
        return httpx.Response(404)
