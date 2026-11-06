"""A model stored with a space in its name is found in the model sets (#3335).

Discovery and the add-printer dropdown store an H2D Pro as "H2D Pro". The
checks upper-cased it to "H2D PRO", which no set holds, so an H2D Pro had no
chamber temperature, no chamber heater and no airduct. Every check in
printer_manager now compares the name without spaces.
"""

import pytest

from backend.app.services import printer_manager as pm
from backend.app.services.printer_manager import (
    drying_screen_only,
    has_stg_cur_idle_bug,
    supports_airduct,
    supports_chamber_heater,
    supports_chamber_temp,
    supports_drying,
    supports_drying_while_printing,
)

H2D_PRO = ["H2D Pro", "h2d pro", " H2D Pro ", "H2DPRO", "O1E", "O2D"]


@pytest.mark.parametrize("model", H2D_PRO)
def test_an_h2d_pro_has_its_chamber_and_airduct(model):
    assert supports_chamber_temp(model)
    assert supports_chamber_heater(model)
    assert supports_airduct(model)


@pytest.mark.parametrize("model", H2D_PRO)
def test_an_h2d_pro_dries_while_printing(model):
    assert supports_drying_while_printing(model, "01.02.00.00")
    assert not supports_drying_while_printing(model, "01.01.09.00")


@pytest.mark.parametrize("model", ["A1 Mini", "a1 mini", "A1MINI", "A1-MINI", "N1"])
def test_an_a1_mini_is_still_an_a1_mini(model):
    assert has_stg_cur_idle_bug(model)
    assert not supports_drying(model, "99.99.99.99")
    assert not supports_chamber_temp(model)


def test_the_x1c_code_keeps_its_dash():
    assert supports_chamber_temp("BL-P001")
    assert supports_drying_while_printing("BL-P001", "01.11.02.00")


def test_a_p1s_still_dries_only_from_its_screen():
    assert drying_screen_only("p1s")
    assert not supports_drying("P1S", "99.99.99.99")


@pytest.mark.parametrize("model", ["X1E", "x1e", "C13"])
def test_the_x1e_heats_its_chamber_but_has_no_airduct(model):
    assert supports_chamber_heater(model)
    assert supports_chamber_temp(model)
    assert not supports_airduct(model)


@pytest.mark.parametrize("model", ["X1C", "BL-P001", "P2S", "N7"])
def test_sensor_only_chambers_have_no_heater(model):
    assert supports_chamber_temp(model)
    assert not supports_chamber_heater(model)


def _model_tables():
    for name in dir(pm):
        value = getattr(pm, name)
        if "MODEL" in name or "FIRMWARE" in name:
            if isinstance(value, (frozenset, set, dict)):
                yield name, value


def test_no_set_holds_a_name_the_checks_cannot_match():
    tables = dict(_model_tables())
    assert len(tables) >= 8  # the scan finds the sets at all

    unmatchable = {
        name: sorted(key for key in table if key != key.strip().upper().replace(" ", ""))
        for name, table in tables.items()
    }
    assert {name: keys for name, keys in unmatchable.items() if keys} == {}
