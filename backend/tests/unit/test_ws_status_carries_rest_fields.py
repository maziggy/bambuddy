"""The live status push carries what the printer card reads from the status (PSF-3, PSF-4).

The websocket push replaces the fields it carries in the card's cached status.
``airduct_mode`` was missing, so switching cooling/heating on the printer's
screen showed the old mode until the next REST poll; ``nozzle_rack[]`` lacked
``filament_type``, and since the push replaces the whole rack an H2C slot
lost its material label on every push.
"""

import backend.app.core.database  # noqa: F401  -- model import order
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_manager import printer_state_to_dict


def test_the_airduct_mode_is_pushed():
    state = PrinterState()
    state.airduct_mode = 1

    assert printer_state_to_dict(state)["airduct_mode"] == 1


def test_a_rack_slot_keeps_its_material():
    state = PrinterState()
    state.nozzle_rack = [{"id": 2, "type": "HS", "diameter": "0.4", "filament_id": "GFL99", "filament_type": "PLA"}]

    [slot] = printer_state_to_dict(state)["nozzle_rack"]
    assert slot["filament_type"] == "PLA"
