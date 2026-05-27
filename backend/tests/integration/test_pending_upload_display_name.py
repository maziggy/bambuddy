"""Integration tests for #1152 follow-up — pending-upload review card name
matches the eventual archive's print_name.

Before this change the review card always showed the raw FTP filename while
the archive's ``print_name`` was resolved from the 3MF metadata title (or, with
the toggle on ``filename``, the stripped stem). That gave users two different
names for the same item — they'd see *Plate_1.gcode* in review and *Some
Creator's Title* in the archive grid.

These tests pin the new contract:
  - ``PendingUploadResponse.display_name`` mirrors what archive_print will
    eventually write to ``PrintArchive.print_name``.
  - The toggle (``virtual_printer_archive_name_source``) flips both views in
    lockstep — never one without the other.
  - Filename normalisation (``Plate_1.gcode.3mf`` → ``Plate_1``) is applied
    consistently regardless of the toggle.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.pending_upload import PendingUpload
from backend.app.models.settings import Settings


async def _set_archive_name_source(db: AsyncSession, value: str) -> None:
    """Write the virtual_printer_archive_name_source setting directly."""
    db.add(Settings(key="virtual_printer_archive_name_source", value=value))
    await db.commit()


async def _seed_pending(
    db: AsyncSession,
    *,
    filename: str,
    metadata_print_name: str | None = None,
) -> int:
    pending = PendingUpload(
        filename=filename,
        file_path=f"/test/pending/{filename}",
        file_size=42,
        source_ip="192.168.1.50",
        status="pending",
        metadata_print_name=metadata_print_name,
    )
    db.add(pending)
    await db.commit()
    await db.refresh(pending)
    return pending.id


class TestDisplayNameResolution:
    """``GET /pending-uploads/`` resolves ``display_name`` to the same value
    ``archive_print`` would store on the eventual ``PrintArchive``."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_default_toggle_uses_metadata_title_when_present(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Default toggle is ``metadata`` — review card shows the embedded
        title rather than the FTP filename, matching what the archived
        PrintArchive.print_name will end up being."""
        await _seed_pending(
            db_session,
            filename="Plate_1.gcode.3mf",
            metadata_print_name="Custom Cool Benchy",
        )

        resp = await async_client.get("/api/v1/pending-uploads/")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["display_name"] == "Custom Cool Benchy"
        # filename is still surfaced separately so the user can see what
        # actually arrived over FTP if they want to (tooltip).
        assert rows[0]["filename"] == "Plate_1.gcode.3mf"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_default_toggle_falls_back_to_stripped_stem_when_no_metadata(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """No embedded title (Bambu Studio's default plate export) — both
        review and archive end up showing the stripped filename stem."""
        await _seed_pending(
            db_session,
            filename="Plate_1.gcode.3mf",
            metadata_print_name=None,
        )

        resp = await async_client.get("/api/v1/pending-uploads/")
        assert resp.status_code == 200
        # ``Plate_1`` — both ``.gcode`` and ``.3mf`` stripped (#1152
        # follow-up: ``Path.stem`` only strips the last suffix and would
        # leave ``Plate_1.gcode``).
        assert resp.json()[0]["display_name"] == "Plate_1"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_filename_toggle_overrides_metadata_title(self, async_client: AsyncClient, db_session: AsyncSession):
        """When the operator opts into ``filename`` (so a user-renamed
        Bambu Studio job surfaces its renamed-at-send filename), the embedded
        creator-baked title is ignored. Review must follow the same toggle
        as the archive."""
        await _set_archive_name_source(db_session, "filename")
        await _seed_pending(
            db_session,
            filename="MyRenamedJob.3mf",
            metadata_print_name="Original Creator Title",
        )

        resp = await async_client.get("/api/v1/pending-uploads/")
        assert resp.status_code == 200
        assert resp.json()[0]["display_name"] == "MyRenamedJob"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_filename_toggle_strips_double_suffix(self, async_client: AsyncClient, db_session: AsyncSession):
        """Filename mode also drops .gcode.3mf — same normalisation as the
        archive's print_name, so the names line up exactly."""
        await _set_archive_name_source(db_session, "filename")
        await _seed_pending(db_session, filename="Plate_4.gcode.3mf", metadata_print_name=None)

        resp = await async_client.get("/api/v1/pending-uploads/")
        assert resp.status_code == 200
        assert resp.json()[0]["display_name"] == "Plate_4"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_one_returns_display_name(self, async_client: AsyncClient, db_session: AsyncSession):
        """The single-resource endpoint surfaces display_name too — UIs that
        load a pending upload by id (e.g. detail modals) get the same name."""
        upload_id = await _seed_pending(
            db_session,
            filename="X.gcode.3mf",
            metadata_print_name="Deep Detail Bear",
        )

        resp = await async_client.get(f"/api/v1/pending-uploads/{upload_id}")
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Deep Detail Bear"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_blank_metadata_title_falls_back_to_stem(self, async_client: AsyncClient, db_session: AsyncSession):
        """A whitespace-only metadata title behaves like absent metadata —
        guards against 3MFs with broken/empty Title fields surfacing as a
        blank review card."""
        await _seed_pending(
            db_session,
            filename="empty-title.gcode.3mf",
            metadata_print_name="   ",
        )

        resp = await async_client.get("/api/v1/pending-uploads/")
        assert resp.status_code == 200
        assert resp.json()[0]["display_name"] == "empty-title"
