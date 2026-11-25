"""A virtual printer's serial is redacted in the support package (#3526).

Its serial is not stored whole -- it is the model's prefix plus the VP's
``serial_suffix`` -- and the A2L's ``26A19A`` and the other newer prefixes are
past what the generic serial pattern matches, so the certificate log lines
carried it unredacted.
"""

import pytest

from backend.app.models.virtual_printer import VirtualPrinter
from backend.app.services.log_reader import collect_sensitive_strings, sanitize_log_content


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["N9", "N7", "O1D", "N6", "O1C2", None])
async def test_the_virtual_printer_serial_is_redacted(db_session, model):
    from backend.app.services.virtual_printer.manager import DEFAULT_VIRTUAL_PRINTER_MODEL, _get_serial_for_model

    db_session.add(VirtualPrinter(name="Shop VP", model=model, serial_suffix="491800007"))
    await db_session.commit()
    serial = _get_serial_for_model(model or DEFAULT_VIRTUAL_PRINTER_MODEL, "491800007")

    sensitive = await collect_sensitive_strings(db_session)
    line = f"Generating certificates for virtual printer (serial: {serial})... Printer: CN={serial}"
    assert sanitize_log_content(line, sensitive) == (
        "Generating certificates for virtual printer (serial: [SERIAL])... Printer: CN=[SERIAL]"
    )


@pytest.mark.asyncio
async def test_the_serial_from_before_a_model_change_is_redacted(db_session):
    """The log may predate the VP's current model."""
    from backend.app.services.virtual_printer.manager import _get_serial_for_model

    db_session.add(VirtualPrinter(name="Shop VP", model="N9", serial_suffix="491800007"))
    await db_session.commit()

    sensitive = await collect_sensitive_strings(db_session)
    old_serial = _get_serial_for_model("N7", "491800007")
    assert sanitize_log_content(f"CN={old_serial}", sensitive) == "CN=[SERIAL]"
