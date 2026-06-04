"""Regression tests for MQTT smart-plug reachability datetime handling."""

from datetime import datetime, timedelta, timezone

from backend.app.services.mqtt_smart_plug import MQTTSmartPlugService, SmartPlugMQTTData


def test_is_reachable_with_fresh_plug_data_does_not_raise():
    """Subscribed plug with no MQTT message yet must not 500 the status endpoint."""
    service = MQTTSmartPlugService()
    service.plug_data[1] = SmartPlugMQTTData(plug_id=1)
    assert service.is_reachable(1) is True


def test_is_reachable_accepts_legacy_naive_last_seen():
    """Rows created before timezone-aware defaults must still compare safely."""
    service = MQTTSmartPlugService()
    service.plug_data[1] = SmartPlugMQTTData(
        plug_id=1,
        last_seen=datetime.utcnow() - timedelta(minutes=1),
    )
    assert service.is_reachable(1) is True