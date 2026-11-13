"""Virtual printer queue mode: start the first job, stage the rest (#3299).

With ``auto_dispatch_first_only`` on, the first file sent to a target that has
nothing queued or printing starts by itself; whatever is sent while the target
has a job running or waiting is staged (``manual_start``) and starts when it is
released one by one. For a multi-plate Send All only the first plate may start
by itself.
"""

import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.database import Base
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.virtual_printer.manager import VirtualPrinterInstance


@pytest.fixture(autouse=True)
def _fake_file_manager_store():
    """Uploads go to the File Manager (library_store); its own tests cover it."""
    stored = MagicMock(id=1, filename="upload.3mf", folder_id=None)
    with (
        patch(
            "backend.app.services.virtual_printer.library_store.store_upload",
            AsyncMock(return_value=(stored, False)),
        ),
        patch(
            "backend.app.services.virtual_printer.library_store.upload_settings",
            AsyncMock(return_value=(None, False)),
        ),
    ):
        yield


def _3mf(path, plates: int) -> None:
    blocks = "".join(f'<plate><metadata key="index" value="{n}"/></plate>' for n in range(1, plates + 1))
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", f"<config>{blocks}</config>")
        for n in range(1, plates + 1):
            zf.writestr(f"Metadata/plate_{n}.gcode", "; gcode\n")


def _factory(added: list):
    """A session factory whose queue is empty and which records what is added."""

    class _Db:
        _next_id = 1

        def add(self, item):
            added.append(item)

        async def execute(self, *_args, **_kwargs):
            result = MagicMock()
            result.scalar = MagicMock(return_value=0)
            return result

        async def flush(self):
            for item in added:
                if getattr(item, "id", None) is None:
                    item.id = self._next_id
                    self._next_id += 1

        async def commit(self):
            return None

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=_Db())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


async def _send(tmp_path, *, plates=1, has_work=False, printing=False, **vp):
    """Send a file to a queue-mode VP; return the queue items it made."""
    added: list = []
    inst = VirtualPrinterInstance(
        vp_id=60,
        name="First",
        mode="queue",
        model="C12",
        access_code="12345678",
        serial_suffix="391800060",
        target_printer_id=vp.pop("target_printer_id", 1),
        base_dir=tmp_path,
        session_factory=_factory(added),
        **vp,
    )
    file_path = tmp_path / "job.3mf"
    _3mf(file_path, plates)
    with (
        patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=None),
        patch("backend.app.core.auth.queue_review_required_for", return_value=False),
        patch("backend.app.core.websocket.ws_manager.send_library_files_changed", new_callable=AsyncMock),
        patch.object(inst, "_target_has_queued_work", AsyncMock(return_value=has_work)),
        patch.object(inst, "_target_is_printing", AsyncMock(return_value=printing)),
    ):
        await inst._add_to_print_queue(file_path, "192.168.1.100")
    return [item for item in added if isinstance(item, PrintQueueItem)]


class TestFirstJobStartsItself:
    @pytest.mark.asyncio
    async def test_the_first_job_to_an_empty_target_starts_by_itself(self, tmp_path):
        items = await _send(tmp_path, auto_dispatch=True, auto_dispatch_first_only=True)

        assert [i.manual_start for i in items] == [False]

    @pytest.mark.asyncio
    async def test_a_job_sent_while_the_target_has_work_is_staged(self, tmp_path):
        items = await _send(tmp_path, auto_dispatch=True, auto_dispatch_first_only=True, has_work=True)

        assert [i.manual_start for i in items] == [True]

    @pytest.mark.asyncio
    async def test_a_job_sent_while_the_target_prints_outside_the_queue_is_staged(self, tmp_path):
        """A print started on the printer or from the slicer has no queue item, and still counts."""
        items = await _send(tmp_path, plates=2, auto_dispatch=True, auto_dispatch_first_only=True, printing=True)

        assert [i.manual_start for i in items] == [True, True]

    @pytest.mark.asyncio
    async def test_only_the_first_plate_of_a_send_all_starts_by_itself(self, tmp_path):
        items = await _send(tmp_path, plates=3, auto_dispatch=True, auto_dispatch_first_only=True)

        assert [i.plate_id for i in items] == [1, 2, 3]
        assert [i.manual_start for i in items] == [False, True, True]

    @pytest.mark.asyncio
    async def test_every_plate_is_staged_when_the_target_has_work(self, tmp_path):
        items = await _send(tmp_path, plates=3, auto_dispatch=True, auto_dispatch_first_only=True, has_work=True)

        assert [i.manual_start for i in items] == [True, True, True]


class TestOtherModesAreUnchanged:
    @pytest.mark.asyncio
    async def test_auto_dispatch_on_starts_every_plate_itself(self, tmp_path):
        items = await _send(tmp_path, plates=3, auto_dispatch=True, has_work=True)

        assert [i.manual_start for i in items] == [False, False, False]

    @pytest.mark.asyncio
    async def test_auto_dispatch_off_stages_everything(self, tmp_path):
        items = await _send(tmp_path, plates=2, auto_dispatch=False)

        assert [i.manual_start for i in items] == [True, True]

    @pytest.mark.asyncio
    async def test_the_option_does_nothing_with_auto_dispatch_off(self, tmp_path):
        items = await _send(tmp_path, auto_dispatch=False, auto_dispatch_first_only=True)

        assert [i.manual_start for i in items] == [True]

    @pytest.mark.asyncio
    async def test_the_queue_is_not_asked_when_the_option_is_off(self, tmp_path):
        added: list = []
        inst = VirtualPrinterInstance(
            vp_id=61,
            name="Plain",
            mode="queue",
            model="C12",
            access_code="12345678",
            serial_suffix="391800061",
            target_printer_id=1,
            auto_dispatch=True,
            base_dir=tmp_path,
            session_factory=_factory(added),
        )
        file_path = tmp_path / "job.3mf"
        _3mf(file_path, 1)
        ask = AsyncMock(return_value=True)
        with (
            patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=None),
            patch("backend.app.core.auth.queue_review_required_for", return_value=False),
            patch("backend.app.core.websocket.ws_manager.send_library_files_changed", new_callable=AsyncMock),
            patch.object(inst, "_target_has_queued_work", ask),
        ):
            await inst._add_to_print_queue(file_path, "192.168.1.100")

        ask.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_job_that_needs_review_waits_even_as_the_first(self, tmp_path):
        added: list = []
        inst = VirtualPrinterInstance(
            vp_id=62,
            name="Review",
            mode="queue",
            model="C12",
            access_code="12345678",
            serial_suffix="391800062",
            target_printer_id=1,
            auto_dispatch=True,
            auto_dispatch_first_only=True,
            base_dir=tmp_path,
            session_factory=_factory(added),
        )
        file_path = tmp_path / "job.3mf"
        _3mf(file_path, 1)
        with (
            patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=None),
            patch("backend.app.core.auth.queue_review_required_for", return_value=True),
            patch("backend.app.core.websocket.ws_manager.send_library_files_changed", new_callable=AsyncMock),
            patch.object(inst, "_target_has_queued_work", AsyncMock(return_value=False)),
        ):
            await inst._add_to_print_queue(file_path, "192.168.1.100", user_id=7)

        assert [i.manual_start for i in added if isinstance(i, PrintQueueItem)] == [True]


@pytest.fixture
async def db():
    """A real database with printers 1 and 2 (P1S) and 3 (X1C)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        for idx, model in ((1, "P1S"), (2, "P1S"), (3, "X1C")):
            session.add(
                Printer(
                    id=idx,
                    name=f"P{idx}",
                    serial_number=f"SERIAL{idx}",
                    ip_address=f"192.168.1.{idx}",
                    access_code="12345678",
                    model=model,
                )
            )
        await session.commit()
        yield session
    await engine.dispose()


def _instance(tmp_path, **kwargs):
    return VirtualPrinterInstance(
        vp_id=63,
        name="Ask",
        mode="queue",
        model="C12",
        access_code="12345678",
        serial_suffix="391800063",
        base_dir=tmp_path,
        session_factory=MagicMock(),
        **kwargs,
    )


class TestTargetHasQueuedWork:
    """The question itself, against a real database."""

    def _inst(self, tmp_path, **kwargs):
        return _instance(tmp_path, **kwargs)

    async def _item(self, db, status="pending", **kwargs):
        db.add(PrintQueueItem(status=status, **kwargs))
        await db.commit()

    @pytest.mark.asyncio
    async def test_an_empty_queue_has_no_work(self, db, tmp_path):
        assert await self._inst(tmp_path, target_printer_id=1)._target_has_queued_work(db, None) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["pending", "printing"])
    async def test_a_pending_or_printing_job_on_the_printer_is_work(self, db, tmp_path, status):
        await self._item(db, status=status, printer_id=1, library_file_id=None)

        assert await self._inst(tmp_path, target_printer_id=1)._target_has_queued_work(db, None) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "skipped"])
    async def test_a_finished_job_is_not_work(self, db, tmp_path, status):
        await self._item(db, status=status, printer_id=1, library_file_id=None)

        assert await self._inst(tmp_path, target_printer_id=1)._target_has_queued_work(db, None) is False

    @pytest.mark.asyncio
    async def test_a_staged_job_waiting_for_its_play_is_work(self, db, tmp_path):
        await self._item(db, printer_id=1, library_file_id=None, manual_start=True)

        assert await self._inst(tmp_path, target_printer_id=1)._target_has_queued_work(db, None) is True

    @pytest.mark.asyncio
    async def test_another_printers_work_does_not_count(self, db, tmp_path):
        await self._item(db, printer_id=2, library_file_id=None)

        assert await self._inst(tmp_path, target_printer_id=1)._target_has_queued_work(db, None) is False

    @pytest.mark.asyncio
    async def test_for_any_model_work_aimed_at_the_model_counts(self, db, tmp_path):
        await self._item(db, printer_id=None, target_model="P1S", library_file_id=None)

        assert await self._inst(tmp_path)._target_has_queued_work(db, "P1S") is True
        assert await self._inst(tmp_path)._target_has_queued_work(db, "X1C") is False

    @pytest.mark.asyncio
    async def test_for_any_model_work_on_a_printer_of_the_model_counts(self, db, tmp_path):
        await self._item(db, printer_id=2, library_file_id=None)

        assert await self._inst(tmp_path)._target_has_queued_work(db, "P1S") is True
        assert await self._inst(tmp_path)._target_has_queued_work(db, "X1C") is False


def _printers(states: dict[int, str | None]):
    """A printer manager with these printers connected (a state) or not (None)."""
    pm = MagicMock()
    pm.is_connected = MagicMock(side_effect=lambda pid: states.get(pid) is not None)
    pm.get_status = MagicMock(side_effect=lambda pid: MagicMock(state=states[pid]) if states.get(pid) else None)
    pm.get_all_statuses = MagicMock(
        return_value={pid: MagicMock(state=state) for pid, state in states.items() if state is not None}
    )
    return pm


class TestTargetIsPrinting:
    """The live state: a print the queue did not start still makes the target busy."""

    def _inst(self, tmp_path, states=None, **kwargs):
        inst = _instance(tmp_path, **kwargs)
        inst._printer_manager = _printers(states or {})
        return inst

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["RUNNING", "PAUSE", "PREPARE", "SLICING"])
    async def test_a_fixed_target_that_prints_is_busy(self, db, tmp_path, state):
        inst = self._inst(tmp_path, {1: state}, target_printer_id=1)

        assert await inst._target_is_printing(db, None) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["IDLE", "FINISH", "FAILED"])
    async def test_a_fixed_target_that_is_idle_is_not_busy(self, db, tmp_path, state):
        inst = self._inst(tmp_path, {1: state}, target_printer_id=1)

        assert await inst._target_is_printing(db, None) is False

    @pytest.mark.asyncio
    async def test_a_disconnected_fixed_target_is_not_busy(self, db, tmp_path):
        inst = self._inst(tmp_path, {1: None}, target_printer_id=1)

        assert await inst._target_is_printing(db, None) is False

    @pytest.mark.asyncio
    async def test_another_printer_printing_does_not_make_a_fixed_target_busy(self, db, tmp_path):
        inst = self._inst(tmp_path, {1: "IDLE", 2: "RUNNING"}, target_printer_id=1)

        assert await inst._target_is_printing(db, None) is False

    @pytest.mark.asyncio
    async def test_for_any_model_a_printer_of_the_model_printing_is_busy(self, db, tmp_path):
        inst = self._inst(tmp_path, {1: "IDLE", 2: "RUNNING", 3: "IDLE"})

        assert await inst._target_is_printing(db, "P1S") is True
        assert await inst._target_is_printing(db, "X1C") is False

    @pytest.mark.asyncio
    async def test_for_any_model_a_disconnected_printer_does_not_count(self, db, tmp_path):
        inst = self._inst(tmp_path, {1: "IDLE", 2: None})

        assert await inst._target_is_printing(db, "P1S") is False

    @pytest.mark.asyncio
    async def test_without_a_printer_manager_nothing_is_busy(self, db, tmp_path):
        inst = self._inst(tmp_path, target_printer_id=1)
        inst._printer_manager = None

        assert await inst._target_is_printing(db, None) is False
