import pytest
from pydantic import ValidationError

from backend.app.schemas.settings import AppSettingsUpdate


def test_default_sidebar_order_accepts_hidden_system_item_ids():
    value = '{"order":["printers","ext-1","settings"],"hiddenSystemItemIds":["stats"]}'

    update = AppSettingsUpdate(default_sidebar_order=value)

    assert update.default_sidebar_order == value


def test_default_sidebar_order_rejects_invalid_hidden_system_item_ids():
    with pytest.raises(ValidationError):
        AppSettingsUpdate(default_sidebar_order='{"order":["printers"],"hiddenSystemItemIds":"stats"}')
