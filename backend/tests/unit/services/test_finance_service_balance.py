"""Unit tests for how a balance is reported."""

import pytest

from backend.app.models.settings import Settings
from backend.app.schemas.settings import AppSettings as AppSettingsSchema
from backend.app.services.finance_balance import resolve_configured_currency


class TestConfiguredCurrency:
    """#3123: finance is not allowed its own idea of the currency.

    Every other surface renders the ``currency`` app setting. Finance answered
    from a per-wallet column instead, which three of its four writers filled
    with a hardcoded "EUR", so an install configured for AUD reported euros.
    The column is gone and this function is what replaced it.
    """

    @pytest.mark.asyncio
    async def test_reads_the_configured_currency(self, db_session):
        db_session.add(Settings(key="currency", value="AUD"))
        await db_session.commit()

        assert await resolve_configured_currency(db_session) == "AUD"

    @pytest.mark.asyncio
    async def test_falls_back_to_the_app_default_when_unset(self, db_session):
        # The app default is USD, which is also what every frontend fallback
        # uses. The old finance fallback said EUR, which is how an install
        # that never touched the setting still showed euros.
        assert await resolve_configured_currency(db_session) == AppSettingsSchema().currency
        assert await resolve_configured_currency(db_session) == "USD"

    @pytest.mark.asyncio
    async def test_an_empty_setting_row_is_not_a_currency(self, db_session):
        db_session.add(Settings(key="currency", value=""))
        await db_session.commit()

        assert await resolve_configured_currency(db_session) == "USD"
