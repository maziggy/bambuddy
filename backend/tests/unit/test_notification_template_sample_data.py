"""Every event_type in EVENT_VARIABLES needs a matching SAMPLE_DATA entry.

Regression: location_ha_sensor_alert (#2824) was added to EVENT_VARIABLES but
never given a SAMPLE_DATA entry. The template preview endpoint falls back to
an empty sample dict for an event type it has no data for, so every
{placeholder} in the template silently disappears — "{location}: {sensor} is
{state}" rendered as just ": is " with nothing to say what did what.
"""

from backend.app.schemas.notification_template import EVENT_VARIABLES, SAMPLE_DATA


def test_every_event_type_has_sample_data():
    missing = sorted(set(EVENT_VARIABLES) - set(SAMPLE_DATA))
    assert missing == [], f"EVENT_VARIABLES entries with no SAMPLE_DATA: {missing}"


def test_sample_data_covers_every_variable_its_event_type_declares():
    # A partial sample (declared variable missing a sample value) produces
    # the same silent-blank symptom as a missing sample entirely.
    incomplete = {
        event_type: sorted(set(variables) - set(SAMPLE_DATA.get(event_type, {})))
        for event_type, variables in EVENT_VARIABLES.items()
    }
    incomplete = {k: v for k, v in incomplete.items() if v}
    assert incomplete == {}, f"Event types with variables missing from their sample data: {incomplete}"
