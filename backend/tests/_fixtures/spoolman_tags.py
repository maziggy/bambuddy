"""A Spoolman that links tags natively the way 0.27 does, or not at all like older servers.

The rules modelled here are the server's own (``spoolman/api/v1/spool.py`` and
``spoolman/database/tag.py`` in Spoolman 0.27):

- ``GET /api/v1/tag/reader`` answers 200 on 0.27; an older server has no such route.
- a UID is stored without separators and upper-cased, and must be hex, else 400
- a UID identifies exactly one spool, filament or location, archived spools
  included: linking one that something else holds answers 409 with ``spool_id`` or
  ``filament_id`` naming the holder, and with neither for a location
- re-linking a UID to the spool that already holds it succeeds and changes nothing
- ``GET /api/v1/spool?tag=<uid>`` answers the spool holding it, server side, and
  like every spool listing leaves archived spools out unless ``allow_archived=true``
- each spool carries its native tags under ``tags``

An older server ignores the ``tag`` query parameter, as FastAPI does with any
parameter a route does not declare, and answers the whole list.

Failures a test needs can be switched on: ``refuse_400`` (UIDs the tag route
rejects as invalid) and ``fail_patch`` (spool ids whose PATCH answers 500).

Every request is logged as ``"METHOD /path"`` (``?tag=`` included), so a test can
assert what was *not* asked: a scan that must not load the whole inventory, say.
"""

import json
import re

import httpx

from backend.app.services.spoolman import SpoolmanClient

BASE_URL = "http://spoolman.test:7912"

_HEX = re.compile(r"^[0-9A-F]+$")


def _normalise(uid: str) -> str:
    return re.sub(r"[\s:\-]", "", uid).upper()


class FakeSpoolman:
    def __init__(self, *, tag_api: bool = True):
        self.tag_api = tag_api
        self.spools: dict[int, dict] = {}
        # UIDs held by a filament or a location rather than a spool: the other 409s.
        self.filament_tags: dict[str, int] = {}
        self.location_tags: set[str] = set()
        self.refuse_400: set[str] = set()
        self.fail_patch: set[int] = set()
        self.log: list[str] = []

    def add_spool(self, spool_id: int, *, extra_tag: str | None = None, tags=(), archived: bool = False) -> dict:
        spool = {
            "id": spool_id,
            "filament": {
                "id": 1,
                "name": "PLA Basic",
                "material": "PLA",
                "color_hex": "FF0000",
                "weight": 1000.0,
                "spool_weight": 196.0,
                "vendor": {"id": 1, "name": "Bambu Lab"},
            },
            "remaining_weight": 800.0,
            "used_weight": 200.0,
            "archived": archived,
            "registered": "2026-09-26T00:00:00Z",
            "extra": {"tag": json.dumps(extra_tag)} if extra_tag is not None else {},
            "tags": [],
        }
        if self.tag_api:
            spool["tags"] = [{"uid": _normalise(u)} for u in tags]
        else:
            del spool["tags"]
        self.spools[spool_id] = spool
        return spool

    def native(self, spool_id: int) -> list[str]:
        return sorted(t["uid"] for t in self.spools[spool_id].get("tags", []))

    def holder(self, uid: str) -> int | None:
        return next((s["id"] for s in self.spools.values() if uid in self.native(s["id"])), None)

    def asked(self, entry: str) -> int:
        return self.log.count(entry)

    def tag_writes(self) -> list[str]:
        return [e for e in self.log if "/tag" in e and not e.startswith("GET /tag/reader")]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        tag_param = request.url.params.get("tag")
        self.log.append(f"{request.method} {path}" + (f"?tag={tag_param}" if tag_param else ""))
        body = json.loads(request.content) if request.content else {}

        if path == "/health":
            return httpx.Response(200, json={"status": "healthy"})

        tag_route = path == "/tag/reader" or re.fullmatch(r"/spool/\d+/tag(/.*)?", path)
        if tag_route and not self.tag_api:
            return httpx.Response(404, json={"detail": "Not Found"})

        if path == "/tag/reader":
            return httpx.Response(200, json=[])

        if path == "/spool" and request.method == "GET":
            if tag_param is not None and self.tag_api:
                uid = _normalise(tag_param)
                if not _HEX.match(uid):
                    return httpx.Response(400, json={"message": f"Invalid tag UID {tag_param!r}."})
            allow_archived = request.url.params.get("allow_archived") == "true"
            if tag_param is not None and self.tag_api:
                holder = self.holder(uid)
                found = [self.spools[holder]] if holder is not None else []
                return httpx.Response(200, json=[x for x in found if allow_archived or not x.get("archived")])
            return httpx.Response(
                200, json=[s for s in self.spools.values() if allow_archived or not s.get("archived")]
            )

        if m := re.fullmatch(r"/spool/(\d+)/tag", path):
            spool_id = int(m.group(1))
            if spool_id not in self.spools:
                return httpx.Response(404, json={"message": f"No spool with ID {spool_id} found."})
            uid = _normalise(body.get("uid", ""))
            if not uid or not _HEX.match(uid) or uid in self.refuse_400:
                return httpx.Response(400, json={"message": f"Invalid tag UID {body.get('uid')!r}."})
            if uid in self.location_tags:
                return httpx.Response(409, json={"message": f"Tag {uid} is already linked to location Shelf."})
            if uid in self.filament_tags:
                return httpx.Response(
                    409, json={"message": "Tag is linked to a filament.", "filament_id": self.filament_tags[uid]}
                )
            holder = self.holder(uid)
            if holder is not None and holder != spool_id:
                return httpx.Response(
                    409, json={"message": f"Tag {uid} is already linked to spool {holder}.", "spool_id": holder}
                )
            if holder is None:
                tag = {"uid": uid}
                if body.get("format"):
                    tag["format"] = body["format"]
                self.spools[spool_id]["tags"].append(tag)
            return httpx.Response(201, json={"uid": uid})

        if m := re.fullmatch(r"/spool/(\d+)/tag/([^/]+)", path):
            spool_id, uid = int(m.group(1)), _normalise(m.group(2))
            if spool_id not in self.spools or uid not in self.native(spool_id):
                return httpx.Response(404, json={"message": "Tag not linked to this spool."})
            self.spools[spool_id]["tags"] = [t for t in self.spools[spool_id]["tags"] if t["uid"] != uid]
            return httpx.Response(204)

        if m := re.fullmatch(r"/spool/(\d+)", path):
            spool_id = int(m.group(1))
            if spool_id not in self.spools:
                return httpx.Response(404, json={"message": f"No spool with ID {spool_id} found."})
            spool = self.spools[spool_id]
            if request.method == "PATCH" and spool_id in self.fail_patch:
                return httpx.Response(500, json={"message": "Internal Server Error"})
            if request.method == "PATCH":
                # Spoolman merges extra key by key, like the real one.
                extra = body.pop("extra", None)
                spool.update(body)
                if extra is not None:
                    spool["extra"] = {**spool.get("extra", {}), **extra}
            return httpx.Response(200, json=spool)

        if path == "/field/spool" and request.method == "GET":
            return httpx.Response(200, json=[{"key": "tag", "name": "tag", "field_type": "text"}])
        if path.startswith("/field/spool/") and request.method == "POST":
            return httpx.Response(200, json={"key": path.rsplit("/", 1)[-1]})

        return httpx.Response(404, json={"detail": f"Not modelled: {request.method} {path}"})


def client_for(fake: FakeSpoolman) -> SpoolmanClient:
    client = SpoolmanClient(BASE_URL)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return client
