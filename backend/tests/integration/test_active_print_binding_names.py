"""Every path that binds a running print registers the same names (AP-3).

A restart resume registered only the archive's file name; the new-archive,
expected and fallback paths each registered a different mix. When MQTT
reported a name the binding path had left out, completion still found the
archive by a name search but lost the binding, and with it the plate restore's
height (#3240).
"""

from types import SimpleNamespace

import pytest

from backend.app import main as main_module
from backend.app.models.archive import PrintArchive
from backend.tests.integration.test_external_print_confirmation_1898 import (  # noqa: F401
    DISPATCH,
    SUBTASK,
    _clear_print_state,
    _drive_print_start,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_a_print_resumed_after_a_restart_is_bound_under_the_printers_names(
    test_engine, db_session, printer_factory
):
    printer_id = (await printer_factory()).id
    archive = PrintArchive(
        printer_id=printer_id,
        filename="My Part.gcode.3mf",
        file_path="archives/test/My Part.gcode.3mf",
        file_size=1,
        print_name=SUBTASK,
        status="printing",
    )
    db_session.add(archive)
    await db_session.commit()

    await _drive_print_start(
        test_engine, SimpleNamespace(id=printer_id), download_ok=False, state_fields={"progress": 40}
    )

    bound = {
        name
        for (pid, name), archive_id in main_module._active_prints.items()
        if pid == printer_id and archive_id == archive.id
    }
    assert {"My Part.gcode.3mf", SUBTASK, f"{SUBTASK}.3mf", DISPATCH} <= bound


def test_the_binding_names_are_one_set():
    main_module._active_prints.clear()

    main_module._bind_active_print(3, 9, "a.gcode.3mf", "/data/Metadata/plate_1.gcode", "a")

    assert {name for (_pid, name) in main_module._active_prints} == {
        "a.gcode.3mf",
        "/data/Metadata/plate_1.gcode",
        "a.3mf",
        "a",
    }
    main_module._active_prints.clear()
