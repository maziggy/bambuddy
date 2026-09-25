"""The slicer plugin's upload API (``api/routes/slicer_plugin.py``).

The route is a thin layer over two things with their own tests -- the library
ingest and ``POST /queue/`` -- so these pin what the layer itself adds: the
manifest, the idempotency claim, the up-front refusals that must leave nothing
behind, and the permission split between uploading and queueing.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.auth import generate_api_key
from backend.app.models.api_key import APIKey
from backend.app.models.library import LibraryFile
from backend.app.models.print_batch import PrintBatch
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.models.slicer_plugin_upload import SlicerPluginUpload
from backend.app.models.user import User

UPLOADS = "/api/v1/slicer-plugin/uploads"
INFO = "/api/v1/slicer-plugin/info"


def _sliced_3mf(plates=(1,), model_id: str = "C11") -> bytes:
    """A minimal sliced 3MF: one G-code member per plate, and a slice_info that
    names the printer it was sliced for (C11 = X1C)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in plates:
            zf.writestr(f"Metadata/plate_{n}.gcode", "; G-code\nG28\n")
        zf.writestr(
            "Metadata/slice_info.config",
            f'<config><plate><metadata key="index" value="{plates[0]}"/>'
            f'<metadata key="printer_model_id" value="{model_id}"/></plate></config>',
        )
    return buf.getvalue()


def _manifest(upload_id: str | None = None, **intent) -> dict:
    return {
        "schema_version": 1,
        "upload_id": upload_id or str(uuid.uuid4()),
        "source": {
            "slicer": "OrcaSlicer",
            "slicer_version": "2.4.0-nightly",
            "plugin_version": "0.1.0",
            "project_name": "Bracket v3",
            "presets": {
                "printer": "Bambu Lab X1 Carbon 0.4 nozzle",
                "process": "0.20mm Standard",
                "filaments": ["PLA"],
            },
            "model_fingerprint": "sha256:" + "a" * 64,
        },
        "intent": intent,
    }


async def _send(
    client: AsyncClient, manifest: dict, content: bytes | None = None, headers=None, name="Bracket.gcode.3mf"
):
    return await client.post(
        UPLOADS,
        files={"file": (name, content if content is not None else _sliced_3mf(), "application/zip")},
        data={"manifest": json.dumps(manifest)},
        headers=headers or {},
    )


async def _count(test_engine, model, *where) -> int:
    """Read through a fresh session -- the fixture's own can look stale after a
    route call commits through its own ``get_db`` session."""
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        return (await fresh.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def _claims(test_engine) -> list[SlicerPluginUpload]:
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as fresh:
        return list((await fresh.execute(select(SlicerPluginUpload))).scalars())


async def _admin_key(client: AsyncClient, db_session, **scopes) -> str:
    """Turn auth on and mint a key owned by the admin, so the key's scope flags
    alone decide what it may do."""
    await client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "plugadmin", "admin_password": "PlugPass1!"},
    )
    admin = (await db_session.execute(select(User).where(User.username == "plugadmin"))).scalar_one()
    flags = {"can_manage_library": True, "can_queue": True, "can_read_status": True}
    flags.update(scopes)
    full_key, key_hash, key_prefix = generate_api_key()
    db_session.add(
        APIKey(
            name=f"plugin-{key_prefix}",
            key_hash=key_hash,
            key_prefix=key_prefix,
            enabled=True,
            user_id=admin.id,
            **flags,
        )
    )
    await db_session.commit()
    return full_key


@pytest.mark.asyncio
@pytest.mark.integration
class TestLibraryUpload:
    async def test_stores_the_file_and_its_provenance(self, async_client, test_engine):
        manifest = _manifest()
        response = await _send(async_client, manifest)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["replayed"] is False
        assert body["queue"] is None and body["queue_error"] is None
        assert body["library_file"]["filename"] == "Bracket.gcode.3mf"

        [claim] = await _claims(test_engine)
        assert claim.upload_id == manifest["upload_id"]
        assert claim.status == "done"
        assert claim.caller == "anonymous"
        assert claim.library_file_id == body["library_file"]["id"]
        assert claim.model_fingerprint == manifest["source"]["model_fingerprint"]
        assert json.loads(claim.presets)["process"] == "0.20mm Standard"
        assert claim.project_name == "Bracket v3"

    async def test_a_retry_returns_the_first_answer_instead_of_a_second_file(self, async_client, test_engine):
        manifest = _manifest()
        first = (await _send(async_client, manifest)).json()
        second = await _send(async_client, manifest)

        assert second.status_code == 200
        assert second.json()["replayed"] is True
        assert second.json()["library_file"] == first["library_file"]
        assert await _count(test_engine, LibraryFile) == 1

    async def test_reports_an_identical_file_already_in_the_library(self, async_client):
        first = (await _send(async_client, _manifest())).json()
        second = (await _send(async_client, _manifest())).json()

        assert second["library_file"]["duplicate_of"] == first["library_file"]["id"]
        assert any(f"#{first['library_file']['id']}" in w for w in second["warnings"])


@pytest.mark.asyncio
@pytest.mark.integration
class TestQueueUpload:
    async def test_queues_the_chosen_plate_as_a_batch_of_copies(self, async_client, printer_factory, test_engine):
        printer = await printer_factory(model="X1C")
        response = await _send(
            async_client,
            _manifest(action="queue", printer_id=printer.id, plate_id=2, copies=3, manual_start=True),
            _sliced_3mf(plates=(1, 2)),
        )

        assert response.status_code == 200, response.text
        queue = response.json()["queue"]
        assert len(queue["queue_item_ids"]) == 3
        assert queue["batch_id"] is not None

        maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as fresh:
            items = list(
                (
                    await fresh.execute(select(PrintQueueItem).where(PrintQueueItem.id.in_(queue["queue_item_ids"])))
                ).scalars()
            )
        assert {(i.printer_id, i.plate_id, i.manual_start, i.batch_id) for i in items} == {
            (printer.id, 2, True, queue["batch_id"])
        }

    async def test_a_retry_does_not_queue_the_print_again(self, async_client, printer_factory, test_engine):
        """The reason the endpoint is idempotent at all: a send whose response
        was lost is retried, and a second queue insert is a second print."""
        printer = await printer_factory(model="X1C")
        manifest = _manifest(action="queue", printer_id=printer.id, copies=2)
        first = (await _send(async_client, manifest)).json()
        second = (await _send(async_client, manifest)).json()

        assert second["replayed"] is True
        assert second["queue"] == first["queue"]
        assert await _count(test_engine, PrintQueueItem) == 2

    async def test_a_plate_the_file_does_not_have_is_refused_before_anything_is_stored(
        self, async_client, printer_factory, test_engine
    ):
        printer = await printer_factory(model="X1C")
        response = await _send(async_client, _manifest(action="queue", printer_id=printer.id, plate_id=3))

        assert response.status_code == 400
        assert "plate 3" in response.json()["detail"]
        assert await _count(test_engine, LibraryFile) == 0
        assert await _claims(test_engine) == []

    async def test_a_refused_queue_keeps_the_file_and_says_why(self, async_client, test_engine):
        """No active printer of the target model: POST /queue/ refuses. The file
        stays in the library -- the user can still queue it from there -- and
        nothing of the half-made queue insert survives."""
        response = await _send(async_client, _manifest(action="queue", target_model="H2D", copies=2))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["queue"] is None
        assert "No active printers" in body["queue_error"]
        assert await _count(test_engine, LibraryFile) == 1
        assert await _count(test_engine, PrintQueueItem) == 0
        assert await _count(test_engine, PrintBatch) == 0

    async def test_a_refusal_after_the_batch_was_made_leaves_no_batch_behind(
        self, async_client, db_session, printer_factory, test_engine
    ):
        """With billing on and no cost centre, POST /queue/ refuses only after it
        has already written the batch for the copies. Without the rollback the
        request's own commit would keep that batch: a batch of nothing."""
        db_session.add(Settings(key="billing_enabled", value="true"))
        await db_session.commit()
        printer = await printer_factory(model="X1C")

        body = (await _send(async_client, _manifest(action="queue", printer_id=printer.id, copies=2))).json()

        assert "Cost center is required" in body["queue_error"]
        assert await _count(test_engine, PrintBatch) == 0
        assert await _count(test_engine, PrintQueueItem) == 0

    async def test_the_cost_center_reaches_the_queue(self, async_client, db_session, printer_factory):
        db_session.add(Settings(key="billing_enabled", value="true"))
        await db_session.commit()
        printer = await printer_factory(model="X1C")

        body = (
            await _send(async_client, _manifest(action="queue", printer_id=printer.id, cost_center_id=424242))
        ).json()

        # Past the "is required" gate, so the id was passed on; refused only
        # because this one does not exist.
        assert body["queue_error"] in ("Cost center not found", "Estimated cost is required for cost center prints")

    async def test_warns_when_the_named_printer_is_not_the_model_it_was_sliced_for(self, async_client, printer_factory):
        printer = await printer_factory(model="H2D", name="Big one")
        body = (await _send(async_client, _manifest(action="queue", printer_id=printer.id))).json()

        assert body["queue"] is not None
        assert any("Sliced for X1C" in w and "Big one" in w for w in body["warnings"])


@pytest.mark.asyncio
@pytest.mark.integration
class TestManifest:
    async def test_an_unknown_manifest_version_is_refused_by_name(self, async_client):
        manifest = _manifest()
        manifest["schema_version"] = 2
        response = await _send(async_client, manifest)
        assert response.status_code == 400
        assert "[1]" in response.json()["detail"]

    async def test_malformed_manifest_is_a_422(self, async_client):
        response = await async_client.post(
            UPLOADS,
            files={"file": ("a.gcode.3mf", _sliced_3mf(), "application/zip")},
            data={"manifest": "{not json"},
        )
        assert response.status_code == 422

    async def test_printer_and_target_model_together_are_refused(self, async_client):
        response = await _send(async_client, _manifest(action="queue", printer_id=1, target_model="P1S"))
        assert response.status_code == 422

    async def test_unknown_keys_are_ignored_so_a_newer_plugin_still_works(self, async_client):
        manifest = _manifest()
        manifest["source"]["something_new"] = {"x": 1}
        assert (await _send(async_client, manifest)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
class TestClaim:
    async def test_a_failed_upload_releases_its_id_for_the_retry(self, async_client, test_engine):
        manifest = _manifest()
        bad = await _send(async_client, manifest, content=b"not a zip")
        assert bad.status_code == 400
        assert await _claims(test_engine) == []

        good = await _send(async_client, manifest)
        assert good.status_code == 200
        assert good.json()["replayed"] is False

    async def test_an_id_still_being_processed_is_refused(self, async_client, db_session):
        upload_id = str(uuid.uuid4())
        db_session.add(SlicerPluginUpload(upload_id=upload_id, caller="anonymous", status="processing"))
        await db_session.commit()

        response = await _send(async_client, _manifest(upload_id))
        assert response.status_code == 409
        assert "still being processed" in response.json()["detail"]

    async def test_an_abandoned_claim_is_taken_over(self, async_client, db_session, test_engine):
        upload_id = str(uuid.uuid4())
        long_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        db_session.add(
            SlicerPluginUpload(upload_id=upload_id, caller="anonymous", status="processing", created_at=long_ago)
        )
        await db_session.commit()

        response = await _send(async_client, _manifest(upload_id))
        assert response.status_code == 200, response.text
        [claim] = await _claims(test_engine)
        assert claim.status == "done"


@pytest.mark.asyncio
@pytest.mark.integration
class TestPermissions:
    async def test_info_without_auth(self, async_client):
        body = (await async_client.get(INFO)).json()
        assert body["manifest_versions"] == [1]
        assert body["can_upload"] is True and body["can_queue"] is True

    async def test_a_key_that_may_not_queue_is_told_so_and_refused_before_anything_is_stored(
        self, async_client, db_session, printer_factory, test_engine
    ):
        key = await _admin_key(async_client, db_session, can_queue=False)
        headers = {"X-API-Key": key}
        printer = await printer_factory(model="X1C")

        info = await async_client.get(INFO, headers=headers)
        assert info.status_code == 200
        assert info.json()["can_queue"] is False

        refused = await _send(async_client, _manifest(action="queue", printer_id=printer.id), headers=headers)
        assert refused.status_code == 403
        assert await _count(test_engine, LibraryFile) == 0
        assert await _claims(test_engine) == []

        stored = await _send(async_client, _manifest(), headers=headers)
        assert stored.status_code == 200

    async def test_a_key_without_library_access_cannot_connect(self, async_client, db_session):
        key = await _admin_key(async_client, db_session, can_manage_library=False)
        assert (await async_client.get(INFO, headers={"X-API-Key": key})).status_code == 403

    async def test_an_upload_id_does_not_replay_to_another_caller(self, async_client, db_session, test_engine):
        key_a = await _admin_key(async_client, db_session)
        full_b, hash_b, prefix_b = generate_api_key()
        owner_id = (await db_session.execute(select(APIKey.user_id))).scalars().first()
        db_session.add(
            APIKey(
                name="plugin-b",
                key_hash=hash_b,
                key_prefix=prefix_b,
                enabled=True,
                user_id=owner_id,
                can_manage_library=True,
                can_queue=True,
            )
        )
        await db_session.commit()

        manifest = _manifest()
        assert (await _send(async_client, manifest, headers={"X-API-Key": key_a})).status_code == 200
        other = await _send(async_client, manifest, headers={"Authorization": f"Bearer {full_b}"})

        assert other.status_code == 409
        assert "library_file" not in other.text
        [claim] = await _claims(test_engine)
        assert claim.caller.startswith("apikey:")
