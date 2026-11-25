"""History retention can no longer be set so that it deletes all history.

``printer_sensor_history_retention_days`` had no UI and, like the AMS one, no
minimum: 0 or less put the daily cleanup's cutoff at now and deleted every row.
Both settings now take at least one day, and the cleanup reads a stored value
the API accepted before as at least one day too.
"""

import pytest
from pydantic import ValidationError

from backend.app.main import _retention_days
from backend.app.schemas.settings import AppSettingsUpdate


@pytest.mark.parametrize("key", ["ams_history_retention_days", "printer_sensor_history_retention_days"])
@pytest.mark.parametrize("value", [0, -5])
def test_less_than_a_day_is_refused(key, value):
    with pytest.raises(ValidationError):
        AppSettingsUpdate(**{key: value})


@pytest.mark.parametrize("key", ["ams_history_retention_days", "printer_sensor_history_retention_days"])
def test_a_day_or_more_is_taken(key):
    assert getattr(AppSettingsUpdate(**{key: 1}), key) == 1


@pytest.mark.parametrize(
    ("stored", "days"),
    [(None, 30), ("90", 90), ("0", 1), ("-3", 1), ("not a number", 30)],
)
def test_the_cleanup_reads_a_stored_value_as_at_least_a_day(stored, days):
    assert _retention_days(stored, 30) == days
