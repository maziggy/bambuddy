"""Bambu internal model codes resolve to the right printer everywhere.

The codes are shared by the SSDP DevModel header (so ``Printer.model`` of an
older install can hold them) and slice_info's ``printer_model_id``. A real P1P
3MF carries ``C11`` next to "Bambu Lab P1P", and the X1 Carbon's code is
``BL-P001``. Several maps had the C-codes shifted onto the wrong printers:
C11/C12 read as X1C/X1, the X1E's C13 checked P2S firmware, and the P2S's N7
was missing, which left a P2S file's ``sliced_for_model`` as the raw code.
"""

import pytest

from backend.app.services.firmware_check import FirmwareCheckService
from backend.app.utils.printer_models import (
    get_rod_type,
    has_ethernet,
    has_remote_storage_toggle,
    is_gcode_compatible,
    normalize_printer_model_id,
)

CODES = [
    ("BL-P001", "X1C"),
    ("BL-P002", "X1"),
    ("C13", "X1E"),
    ("C11", "P1P"),
    ("C12", "P1S"),
    ("N7", "P2S"),
    ("N6", "X2D"),
    ("O1D", "H2D"),
    ("N2S", "A1"),
    ("N1", "A1 Mini"),
]


@pytest.mark.parametrize(("code", "name"), CODES)
def test_code_resolves_to_its_printer(code, name):
    assert normalize_printer_model_id(code) == name


@pytest.mark.parametrize(("code", "name"), CODES)
def test_capabilities_agree_between_code_and_name(code, name):
    assert has_ethernet(code) == has_ethernet(name)
    assert get_rod_type(code) == get_rod_type(name)
    assert has_remote_storage_toggle(code) == has_remote_storage_toggle(name)


def test_the_p1p_has_no_ethernet_and_the_x1c_has():
    assert not has_ethernet("C11")
    assert has_ethernet("BL-P001")


def test_a_p2s_file_can_go_to_a_p2s():
    # A 3MF without printer_model in project_settings keeps the resolved
    # printer_model_id as sliced_for_model; for N7 that used to be "N7",
    # which the dispatch gate compared against "P2S" and refused.
    assert is_gcode_compatible(normalize_printer_model_id("N7"), "P2S")


@pytest.mark.parametrize(
    ("model", "key"),
    [("C13", "x1e"), ("X1E", "x1e"), ("C11", "p1"), ("C12", "p1"), ("N7", "p2s"), ("BL-P001", "x1")],
)
def test_firmware_line_for_code(model, key):
    assert FirmwareCheckService()._resolve_api_key(model) == key
