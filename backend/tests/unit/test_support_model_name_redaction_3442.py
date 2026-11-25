"""A printer named after its model keeps the model in the support package (#3442).

The support package redacts printer names everywhere. A printer named "H3C"
turned every "H3C" into [PRINTER], so the package no longer said which model
it came from. A name that is only a model name is not private and stays.
"""

import pytest

from backend.app.services.log_reader import collect_sensitive_strings, sanitize_log_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("H3C", "H3C"),  # a model Bambuddy does not list yet: the row's own model decides
        ("x1c", "P1S"),  # a listed display name, any case
        ("A1mini", "A1"),  # spaces do not matter
        ("BL-P001", "X1C"),  # an internal code
        ("Bambu Lab H2D", "H2D"),  # the 3MF name
    ],
)
async def test_a_model_name_is_not_redacted(db_session, printer_factory, name, model):
    await printer_factory(name=name, model=model)
    sensitive = await collect_sensitive_strings(db_session)
    assert name not in sensitive


@pytest.mark.asyncio
async def test_a_real_name_is_still_redacted(db_session, printer_factory):
    await printer_factory(name="Workshop H3C", model="H3C")
    sensitive = await collect_sensitive_strings(db_session)
    assert sensitive["Workshop H3C"] == "[PRINTER]"
    line = "Workshop H3C: model H3C connected"
    assert sanitize_log_content(line, sensitive) == "[PRINTER]: model H3C connected"
