"""Obico detection keeps a print's state through a pause (#3459).

Every poll that didn't see RUNNING used to wipe the print's smoothing and its
"action fired", PAUSE included: a print Obico had paused was re-armed on
resume, ran its warmup again, and could be paused again by the same false
positive. The state now ends with the print (FINISH / FAILED / IDLE) or with
a new job, and the printer's long-term baseline outlives its prints.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.obico_detection import ObicoDetectionService
from backend.app.services.obico_smoothing import WARMUP_FRAMES

FAKE_JPEG = b"\xff\xd8\xff\xd9"


def _settings(action: str = "pause") -> dict:
    return {
        "enabled": True,
        "ml_url": "http://obico:3333",
        "sensitivity": "medium",
        "action": action,
        "poll_interval": 10,
        "enabled_printers": None,
        "snapshot_base_url": "http://bambuddy:8000",
    }


def _status(state: str = "RUNNING", task: str = "job", subtask_id: str | None = "111"):
    return MagicMock(state=state, task_name=task, subtask_name="", subtask_id=subtask_id)


class _Rig:
    """Drives _poll_once with a printer status and an ML API answer."""

    def __init__(self, svc: ObicoDetectionService):
        self.svc = svc
        self.p = 0.0
        self.action = AsyncMock()

    async def poll(self, status, settings: dict, times: int = 1) -> None:
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.side_effect = lambda: {"detections": [["failure", self.p, [0, 0, 1, 1]]] if self.p else []}
        client = MagicMock()
        client.get = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        manager = MagicMock()
        manager.get_all_statuses.return_value = {1: status}
        manager.is_connected.return_value = True
        with (
            patch.dict("sys.modules", {"backend.app.services.printer_manager": MagicMock(printer_manager=manager)}),
            patch("backend.app.services.obico_detection.httpx.AsyncClient", return_value=client),
            patch("backend.app.services.obico_actions.execute_action", new=self.action),
            patch.object(self.svc, "_capture_frame", new=AsyncMock(return_value=FAKE_JPEG)),
        ):
            for _ in range(times):
                await self.svc._poll_once(settings)


@pytest.fixture
def rig():
    return _Rig(ObicoDetectionService())


async def _pause_by_obico(rig: _Rig) -> None:
    rig.p = 0.0
    await rig.poll(_status(), _settings(), times=WARMUP_FRAMES + 200)
    rig.p = 3.0
    await rig.poll(_status(), _settings(), times=40)
    assert rig.action.await_count == 1


@pytest.mark.asyncio
async def test_a_resumed_print_keeps_its_state_and_is_not_paused_again(rig):
    await _pause_by_obico(rig)
    frames = rig.svc._states[1].frame_count

    await rig.poll(_status("PAUSE"), _settings(), times=5)
    assert rig.svc._states[1].frame_count == frames  # kept, not checked while paused
    await rig.poll(_status(), _settings(), times=WARMUP_FRAMES + 10)

    assert rig.svc._states[1].frame_count == frames + WARMUP_FRAMES + 10
    assert rig.action.await_count == 1


@pytest.mark.parametrize("ended", ["FINISH", "FAILED", "IDLE", "PREPARE", "SLICING"])
@pytest.mark.asyncio
async def test_the_state_ends_with_the_print(rig, ended):
    await _pause_by_obico(rig)

    await rig.poll(_status(ended), _settings())

    assert 1 not in rig.svc._states
    assert 1 not in rig.svc._action_fired


@pytest.mark.asyncio
async def test_another_job_of_the_same_file_starts_fresh(rig):
    # Its FINISH and PREPARE both fell between two polls; same task name.
    await _pause_by_obico(rig)

    rig.p = 0.0
    await rig.poll(_status(subtask_id="222"), _settings())

    assert rig.svc._states[1].frame_count == 1
    assert rig.svc._action_fired[1] is False


@pytest.mark.asyncio
async def test_a_reprint_without_a_job_id_starts_fresh_after_prepare(rig):
    # A print from the SD card or the touchscreen may report no job id.
    await _pause_by_obico(rig)
    await rig.poll(_status("PREPARE", subtask_id=None), _settings())

    rig.p = 0.0
    await rig.poll(_status(subtask_id=None), _settings())

    assert rig.svc._states[1].frame_count == 1
    assert rig.svc._action_fired[1] is False


@pytest.mark.asyncio
async def test_a_job_id_arriving_late_is_the_same_print(rig):
    await rig.poll(_status(subtask_id=None), _settings(), times=3)
    await rig.poll(_status(subtask_id="111"), _settings(), times=2)

    assert rig.svc._states[1].frame_count == 5


@pytest.mark.asyncio
async def test_the_baseline_carries_into_the_next_print(rig):
    rig.p = 0.82  # the plate artwork, in every frame
    await rig.poll(_status(), _settings(), times=3000)
    await rig.poll(_status("FINISH"), _settings())

    # The next print's camera only shows it once the plate is uncovered: on a
    # printer that had never seen it, that is a sudden rise.
    rig.p = 0.0
    await rig.poll(_status(task="next", subtask_id="222"), _settings("notify"), times=200)
    rig.p = 0.82
    await rig.poll(_status(task="next", subtask_id="222"), _settings("notify"), times=40)

    assert rig.svc._last_class[1] == "safe"
    assert rig.action.await_count == 0


@pytest.mark.asyncio
async def test_the_status_shows_the_score_minus_the_baseline(rig):
    rig.p = 0.82
    await rig.poll(_status(), _settings(), times=WARMUP_FRAMES + 200)

    assert rig.svc.get_per_printer()[1]["score"] == pytest.approx(0.0, abs=1e-3)

    # Once a part covers what the baseline learned, the score drops below 0:
    # shown as no detection, not as a negative number.
    rig.p = 0.3  # still a detection, so it is logged
    await rig.poll(_status(), _settings(), times=20)
    assert rig.svc._states[1].last.score < 0
    assert rig.svc.get_status()["history"]
    assert rig.svc.get_per_printer()[1]["score"] == 0.0
    assert all(entry["score"] >= 0 for entry in rig.svc.get_status()["history"])


def test_the_status_shows_the_pause_level():
    thresholds = ObicoDetectionService().get_status("medium")["thresholds"]
    assert thresholds["pause"] == pytest.approx(0.78 * 1.75)


@pytest.mark.asyncio
async def test_a_new_print_forgets_the_old_job_id(rig):
    # Task name changed while the new job id is still unknown: when it
    # arrives it belongs to this print, not to a third one.
    await rig.poll(_status(subtask_id="111"), _settings(), times=3)
    await rig.poll(_status(task="next", subtask_id=None), _settings(), times=3)
    await rig.poll(_status(task="next", subtask_id="222"), _settings(), times=2)

    assert rig.svc._states[1].frame_count == 5


@pytest.mark.asyncio
async def test_forgetting_a_printer_drops_its_baseline(rig):
    await rig.poll(_status(), _settings(), times=3)
    rig.svc.forget_printer(1)
    assert 1 not in rig.svc._baselines


@pytest.mark.parametrize(("action", "fired"), [("notify", 1), ("pause", 0), ("pause_and_off", 0)])
@pytest.mark.asyncio
async def test_notify_fires_at_the_alert_level_and_pausing_waits(rig, action, fired):
    # A slow build-up to 0.8 over ~25 minutes: Obico alerts, never pauses.
    await rig.poll(_status(), _settings(action), times=1550)
    for i in range(1, 300):
        rig.p = 0.8 * min(1.0, i / 150)
        await rig.poll(_status(), _settings(action))

    assert rig.action.await_count == fired
