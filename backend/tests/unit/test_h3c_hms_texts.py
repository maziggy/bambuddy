"""H3C (serial prefix 32X) fault texts, from Bambu Studio 02.09's hms_en_32X.json.

The catalog was generated from Bambu Studio before the H3C, so its faults --
module 0x2A is the AMS 3H / PrintTool rack -- showed with no description.
"""

from backend.app.services.hms_errors import _CATALOG, describe_fault

H3C = "32X"


def test_the_catalog_has_the_h3c_model_file():
    assert H3C in _CATALOG["_source"]["model_file_versions"]


def test_unload_prompts_seen_on_the_printer_have_text():
    # Shown during an unload from bambuddy on 2026-11-07 (slot 1, PrintTool 1).
    assert describe_fault("2A00200000020004", H3C).startswith("Please pull the filament back through AMS 3H slot 1")
    assert describe_fault("2A00700000020013", H3C).startswith("Filament of AMS-3H failed to unload from PrintTool 1")
    assert describe_fault("2A00C003", H3C).startswith("Please pull the filament out of the PrintTool for AMS 3H slot 1")
    assert describe_fault("2A00801D", H3C).startswith("Failed to unload filament. PrintTool 1 has been placed back")


def test_the_h3c_keeps_its_own_wording_where_it_differs():
    # The H3C has one nozzle on its toolhead: no "left and right" here.
    assert describe_fault("0500803C", H3C).startswith("The current nozzle setting does not match")
