"""Unit tests for the Git backup restore service (#2656).

Focus is on the per-category appliers: natural-key matching, the deliberate
refusal to reuse the backup's primary keys, old_id -> new_id remapping for
dependent rows, overwrite-vs-skip, the settings credential blocklist, and the
K-profile paths that depend on live printers.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.archive import PrintArchive
from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_usage_history import SpoolUsageHistory
from backend.app.models.user import User
from backend.app.schemas.github_backup import GitHubRestoreRequest, RestoreCategory
from backend.app.services.github_restore import (
    _COMPANION_CREDENTIAL_ENV,
    _COMPANION_CREDENTIALS,
    _COMPANION_EXPOSURE_TOGGLES,
    ARCHIVES_PATH,
    SETTINGS_PATH,
    SPOOL_USAGE_PATH,
    SPOOLS_PATH,
    GitHubRestoreService,
    _CategoryTally,
    _is_blocked_setting_key,
    _is_protected_setting_key,
    _is_usable_credential,
    _parse_dt,
    _setting_value_is_true,
    _SettingsPlan,
)


def _service() -> GitHubRestoreService:
    return GitHubRestoreService()


def _messages(tally: _CategoryTally) -> list[str]:
    """The English rendering of each note.

    Notes are ``{code, params, message}`` since they became translatable
    (#2656); asserting on the message keeps these tests readable while
    ``_codes`` covers the half a client actually keys on.
    """
    return [note["message"] for note in tally.notes]


def _codes(tally: _CategoryTally) -> list[str]:
    return [note["code"] for note in tally.notes]


class TestParseDt:
    def test_parses_str_datetime_the_backup_writes(self):
        assert _parse_dt("2026-07-27 06:02:05.123456") == datetime(2026, 7, 27, 6, 2, 5, 123456)

    def test_parses_iso_with_t_separator(self):
        assert _parse_dt("2026-07-27T06:02:05") == datetime(2026, 7, 27, 6, 2, 5)

    @pytest.mark.parametrize("value", ["", None, "not a date", 12345, {}])
    def test_returns_none_for_junk(self, value):
        assert _parse_dt(value) is None

    def test_an_offset_is_normalised_to_naive_utc(self):
        """Every DateTime column here is naive UTC; an aware value cannot be
        written to one without silently shifting the wall clock, nor compared
        against one without raising."""
        assert _parse_dt("2026-07-27T08:02:05+02:00") == datetime(2026, 7, 27, 6, 2, 5)
        assert _parse_dt("2026-07-27T06:02:05+00:00").tzinfo is None


class TestSettingKeyBlocklist:
    @pytest.mark.parametrize(
        "key",
        [
            "bambu_cloud_token",
            "auth_secret_key",
            "ha_token",
            "prometheus_token",
            "printer_access_code",
            "smtp_password",
            "some_api_key",
            "ftp_passphrase",
            "MQTT_SECRET",
        ],
    )
    def test_credential_like_keys_are_blocked(self, key):
        assert _is_blocked_setting_key(key) is True

    @pytest.mark.parametrize(
        "key",
        ["low_stock_threshold", "currency", "theme", "local_backup_enabled", "timezone"],
    )
    def test_ordinary_keys_are_allowed(self, key):
        assert _is_blocked_setting_key(key) is False

    @pytest.mark.parametrize(
        "key",
        ["auth_enabled", "advanced_auth_enabled", "local_login_enabled", "setup_completed"],
    )
    def test_auth_policy_keys_are_protected(self, key):
        # Not credential-shaped, so the secret hints never catch them.
        assert _is_blocked_setting_key(key) is False
        assert _is_protected_setting_key(key) is True

    @pytest.mark.parametrize("key", ["currency", "auth_secret_key", "mqtt_enabled", "prometheus_enabled"])
    def test_protected_set_does_not_swallow_ordinary_or_credential_keys(self, key):
        assert _is_protected_setting_key(key) is False

    @pytest.mark.parametrize(
        "key",
        [
            "ldap_enabled",
            "ldap_server_url",
            "ldap_search_base",
            "ldap_user_filter",
            "ldap_security",
            "ldap_group_mapping",
            "ldap_auto_provision",
            "ldap_ca_cert_path",
            "ldap_default_group",
            "ldap_bind_dn",
            "LDAP_ENABLED",
            "ldap_something_added_later",
        ],
    )
    def test_the_whole_ldap_family_is_protected(self, key):
        """Together these name *which directory decides who you are*.

        ``auth.py`` reads them live from this table on every login, so a restore
        that writes them substitutes the authentication source: point
        ``ldap_server_url`` at another directory, set ``ldap_auto_provision``,
        and ``ldap_default_group`` decides what the account it creates gets.

        The companion rule did not cover this and could not: it pairs
        ``ldap_enabled`` with ``ldap_bind_password`` and asks whether the
        integration will *work*, and an anonymous bind works — so a payload that
        simply omitted the password had its toggle written. Refused by prefix so
        a key added to the LDAP schema later is refused by default, and matched
        case-insensitively because the key comes from the backup's JSON rather
        than from our own writer.
        """
        assert _is_protected_setting_key(key) is True

    def test_ldap_enabled_is_not_also_a_companion_toggle(self):
        """It was, and the pair is what let the family through.

        Kept as a test rather than a comment because re-adding it would read as
        tightening the rule while actually being dead code —
        ``_is_protected_setting_key`` runs first in ``_plan_settings``.
        """
        assert "ldap_enabled" not in _COMPANION_CREDENTIALS

    def test_ha_token_from_env_is_deliberately_not_carved_out(self):
        """Recorded so the review's question about it is not re-litigated.

        ``ha_token_from_env`` looks like a false positive for the ``token`` hint,
        but it is only ever constructed in the settings GET response
        (``get_homeassistant_settings``). It is absent from ``AppSettingsUpdate``
        and so is never a ``Settings`` row — it cannot reach a backup, which
        makes an allowlist entry for it dead code.

        Carving it out would also be a live hole rather than a tidy-up: an
        attacker-authored ``settings/app_settings.json`` could then get a
        ``*token*``-named row written simply by choosing that name. This
        The hints are the primary refusal for every credential the collector
        does not filter, so a name-shaped exception to them is exactly the wrong
        shape of fix.
        """
        assert _is_blocked_setting_key("ha_token_from_env") is True


class TestCategoryTally:
    def test_a_note_carries_code_params_and_english(self):
        tally = _CategoryTally()
        tally.note("noData", "No data of this kind in this backup")
        tally.note("spoolUsageUnresolved", "2 usage record(s) skipped", count=2)

        assert tally.notes == [
            {"code": "noData", "params": {}, "message": "No data of this kind in this backup"},
            {"code": "spoolUsageUnresolved", "params": {"count": 2}, "message": "2 usage record(s) skipped"},
        ]

    def test_notes_are_deduplicated(self):
        tally = _CategoryTally()
        tally.note("noData", "same")
        tally.note("noData", "same")
        assert len(tally.notes) == 1

    def test_the_same_code_with_different_params_is_kept(self):
        """Two printers can both be offline, and the user needs both names."""
        tally = _CategoryTally()
        tally.note("kprofilesPrinterOffline", "A is not connected", printer="A")
        tally.note("kprofilesPrinterOffline", "B is not connected", printer="B")
        assert len(tally.notes) == 2

    def test_notes_are_bounded(self):
        tally = _CategoryTally()
        for i in range(50):
            tally.note("noData", f"note {i}", index=i)
        assert len(tally.notes) == 20


class TestRestoreRequestSchema:
    def test_rejects_empty_category_list(self):
        with pytest.raises(ValueError):
            GitHubRestoreRequest(categories=[])

    def test_deduplicates_categories(self):
        request = GitHubRestoreRequest(
            categories=[RestoreCategory.SPOOLS, RestoreCategory.SPOOLS, RestoreCategory.SETTINGS]
        )
        assert request.categories == [RestoreCategory.SPOOLS, RestoreCategory.SETTINGS]

    def test_defaults_to_head(self):
        assert GitHubRestoreRequest(categories=[RestoreCategory.SPOOLS]).ref == "HEAD"

    @pytest.mark.parametrize("ref", ["HEAD", "abc1234", "a" * 40])
    def test_accepts_valid_refs(self, ref):
        assert GitHubRestoreRequest(ref=ref, categories=[RestoreCategory.SPOOLS]).ref == ref

    @pytest.mark.parametrize("ref", ["abc", "main", "../etc/passwd", "a" * 41, "zzzzzzz", "abc 123"])
    def test_rejects_refs_that_are_not_object_names(self, ref):
        with pytest.raises(ValueError):
            GitHubRestoreRequest(ref=ref, categories=[RestoreCategory.SPOOLS])


class TestRestoreSettings:
    @pytest.mark.asyncio
    async def test_inserts_missing_keys(self, db_session):
        tally = _CategoryTally()
        payload = {"version": "1.0", "settings": {"currency": "EUR", "theme": "dark"}}

        await _service()._restore_settings(db_session, payload, overwrite=False, tally=tally)
        await db_session.commit()

        rows = {s.key: s.value for s in (await db_session.execute(select(Settings))).scalars().all()}
        assert rows == {"currency": "EUR", "theme": "dark"}
        assert tally.restored == 2

    @pytest.mark.asyncio
    async def test_skips_existing_key_when_overwrite_off(self, db_session):
        db_session.add(Settings(key="currency", value="USD"))
        await db_session.commit()
        tally = _CategoryTally()

        await _service()._restore_settings(db_session, {"settings": {"currency": "EUR"}}, overwrite=False, tally=tally)
        await db_session.commit()

        row = (await db_session.execute(select(Settings).where(Settings.key == "currency"))).scalar_one()
        assert row.value == "USD"
        assert tally.skipped == 1
        assert tally.restored == 0

    @pytest.mark.asyncio
    async def test_overwrites_existing_key_when_enabled(self, db_session):
        db_session.add(Settings(key="currency", value="USD"))
        await db_session.commit()
        tally = _CategoryTally()

        await _service()._restore_settings(db_session, {"settings": {"currency": "EUR"}}, overwrite=True, tally=tally)
        await db_session.commit()

        row = (await db_session.execute(select(Settings).where(Settings.key == "currency"))).scalar_one()
        assert row.value == "EUR"
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_credential_keys_are_never_restored(self, db_session):
        """A backup predating the collector's denylist can still contain secrets."""
        tally = _CategoryTally()
        payload = {"settings": {"currency": "EUR", "bambu_cloud_token": "leaked", "ha_token": "leaked"}}

        await _service()._restore_settings(db_session, payload, overwrite=True, tally=tally)
        await db_session.commit()

        keys = {s.key for s in (await db_session.execute(select(Settings))).scalars().all()}
        assert keys == {"currency"}
        # Refusals are notes, not tally rows: the preview never counted these
        # keys, so counting them here would put the total above what the user
        # was shown before they pressed Restore.
        assert tally.skipped == 0
        assert any("credential-like" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_auth_settings_are_never_restored(self, db_session):
        """Restoring auth_enabled=false would disable auth behind the cache's back."""
        db_session.add(Settings(key="auth_enabled", value="true"))
        db_session.add(Settings(key="local_login_enabled", value="true"))
        await db_session.commit()
        tally = _CategoryTally()
        payload = {
            "settings": {
                "currency": "EUR",
                "auth_enabled": "false",
                "advanced_auth_enabled": "false",
                "local_login_enabled": "false",
                "setup_completed": "false",
            }
        }

        await _service()._restore_settings(db_session, payload, overwrite=True, tally=tally)
        await db_session.commit()

        rows = {s.key: s.value for s in (await db_session.execute(select(Settings))).scalars().all()}
        assert rows["auth_enabled"] == "true"
        assert rows["local_login_enabled"] == "true"
        assert "advanced_auth_enabled" not in rows
        assert "setup_completed" not in rows
        assert rows["currency"] == "EUR"
        assert tally.restored == 1
        # As above: refused keys are outside the preview's count, so outside the
        # tally too.
        assert tally.skipped == 0
        assert any("authentication setting" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_missing_payload_is_noted_not_fatal(self, db_session):
        tally = _CategoryTally()
        await _service()._restore_settings(db_session, None, overwrite=True, tally=tally)
        assert tally.restored == 0
        assert _codes(tally) == ["noData"]


class TestSettingValueIsTrue:
    """Only the spellings a reader actually treats as "on" count as on."""

    @pytest.mark.parametrize("value", ["true", "TRUE", " True ", True])
    def test_on(self, value):
        assert _setting_value_is_true(value) is True

    @pytest.mark.parametrize("value", ["false", "1", "on", "yes", "", None, False, 0])
    def test_off(self, value):
        # "1"/"on"/"yes" are deliberately off: no reader in the codebase treats
        # them as on, so restoring one cannot switch anything on either.
        assert _setting_value_is_true(value) is False


class TestUsableCredential:
    @pytest.mark.parametrize("value", ["s3cret", " x "])
    def test_present_values_are_usable(self, value):
        assert _is_usable_credential(value) is True

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_or_blank_is_not(self, value):
        # A present-but-blank prometheus_token row is exactly the `if token:`
        # hole in the metrics route, so it must not count as protection.
        assert _is_usable_credential(value) is False


class TestCompanionCredentials:
    """Toggles whose safety depends on a credential the restore refuses to write.

    ``prometheus_enabled`` is the sharp one. ``/api/v1/metrics`` is a public
    route whose only gate is a non-empty ``prometheus_token``, so restoring the
    toggle onto an instance that has no token row publishes the entire metrics
    body to anyone who can reach the port — and with overwrite *off*, since the
    row is missing rather than present. The other four break an integration
    rather than open one, but they are the same shape.
    """

    async def _restore(self, db, tally=None, overwrite=False, **settings) -> _CategoryTally:
        tally = tally or _CategoryTally()
        await _service()._restore_settings(db, {"settings": settings}, overwrite=overwrite, tally=tally)
        await db.commit()
        return tally

    async def _rows(self, db) -> dict:
        return {s.key: s.value for s in (await db.execute(select(Settings))).scalars().all()}

    # --- The refusal itself ------------------------------------------------

    @pytest.mark.asyncio
    async def test_prometheus_toggle_is_refused_when_its_token_was_skipped(self, db_session):
        """The headline case: overwrite off, empty database, endpoint stays shut."""
        tally = await self._restore(db_session, currency="EUR", prometheus_enabled="true", prometheus_token="s3cret")

        rows = await self._rows(db_session)
        assert rows == {"currency": "EUR"}
        assert any("prometheus_enabled" in note and "switched off" in note for note in _messages(tally))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("toggle,credential", sorted(_COMPANION_CREDENTIALS.items()))
    async def test_every_pair_refuses_its_toggle(self, db_session, toggle, credential, monkeypatch):
        monkeypatch.delenv("HA_TOKEN", raising=False)
        await self._restore(db_session, **{toggle: "true", credential: "s3cret"})
        assert toggle not in await self._rows(db_session)

    @pytest.mark.asyncio
    async def test_an_authored_ldap_payload_cannot_substitute_the_directory(self, db_session):
        """The attack the companion rule could not see, refused end to end.

        Anyone who can write to the backup repository can author this file, and
        the shape that beat the old rule is the natural one for an attacker:
        *omit* ``ldap_bind_password``. They own the directory being pointed at,
        so they need no bind credential from us — and an anonymous bind is a
        working config, which is exactly what the availability rule was built to
        allow through.

        Left unrefused, the next login against a fresh username binds to
        ``ldap_server_url``, ``ldap_auto_provision`` creates the local account,
        and ``ldap_default_group`` decides it is an Administrator. Overwrite-off
        is enough on an instance that never configured LDAP: there are no rows
        to skip.
        """
        tally = await self._restore(
            db_session,
            currency="EUR",
            ldap_enabled="true",
            ldap_server_url="ldaps://evil.example.com:636",
            ldap_security="ldaps",
            ldap_search_base="dc=evil,dc=com",
            ldap_user_filter="(uid={username})",
            ldap_auto_provision="true",
            ldap_default_group="Administrators",
        )

        rows = await self._rows(db_session)
        assert rows == {"currency": "EUR"}, "not one LDAP row may land"
        assert any("authentication" in note.lower() for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_ha_toggle_is_refused_when_the_environment_has_no_token(self, db_session, monkeypatch):
        monkeypatch.delenv("HA_TOKEN", raising=False)
        await self._restore(db_session, ha_enabled="true", ha_token="s3cret", ha_url="http://ha.local")

        rows = await self._rows(db_session)
        assert "ha_enabled" not in rows
        assert rows["ha_url"] == "http://ha.local"

    @pytest.mark.asyncio
    async def test_a_blank_local_credential_row_is_not_usable(self, db_session):
        db_session.add(Settings(key="prometheus_token", value=""))
        await db_session.commit()

        await self._restore(db_session, prometheus_enabled="true", prometheus_token="s3cret")

        assert "prometheus_enabled" not in await self._rows(db_session)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["TRUE", " True ", True])
    async def test_true_is_refused_however_it_is_spelled(self, db_session, value):
        await self._restore(db_session, prometheus_enabled=value, prometheus_token="s3cret")
        assert "prometheus_enabled" not in await self._rows(db_session)

    # --- Ruling 3: the tally counts what the preview counted ---------------

    @pytest.mark.asyncio
    async def test_refusals_are_not_counted_in_the_tally(self, db_session):
        tally = await self._restore(db_session, currency="EUR", prometheus_enabled="true", prometheus_token="s3cret")
        assert (tally.restored, tally.skipped, tally.failed) == (1, 0, 0)

    @pytest.mark.asyncio
    async def test_tally_total_equals_the_preview_item_count(self, db_session):
        """The ruling, encoded: the user is shown a number, and it has to hold.

        Off by three before this change — the two name-based refusals and the
        companion one were all counted as ``skipped`` despite never being in the
        preview's count.
        """
        db_session.add(Settings(key="theme", value="light"))
        await db_session.commit()

        values = {
            "currency": "EUR",  # inserted    -> restored
            "theme": "dark",  # exists, overwrite off -> skipped
            "low_stock_threshold": None,  # no value    -> skipped
            "": "junk",  # unusable key -> failed
            "bambu_cloud_token": "x",  # blocked     -> refused
            "auth_enabled": "false",  # protected   -> refused
            "prometheus_enabled": "true",  # companion   -> refused
            "prometheus_token": "s3cret",  # blocked     -> refused
        }
        item_count, _ = await _service()._count_items(
            db_session, RestoreCategory.SETTINGS, {SETTINGS_PATH: {"settings": values}}
        )

        tally = _CategoryTally()
        await _service()._restore_settings(db_session, {"settings": values}, overwrite=False, tally=tally)
        await db_session.commit()

        assert tally.restored + tally.skipped + tally.failed == item_count
        assert (tally.restored, tally.skipped, tally.failed) == (1, 2, 1)

    @pytest.mark.asyncio
    async def test_the_spools_tally_holds_the_same_invariant(self, db_session):
        """Spools broke it the other way: the tally counted more than the preview.

        ``_restore_spool_usage`` increments this category's tally, but the
        preview counted only the spools and mentioned the usage records in the
        detail — so a backup with any usage history reported a total larger than
        the number the user was shown.
        """
        spools = {
            "spools": [
                {"id": 1, "material": "PLA", "brand": "Bambu Lab", "created_at": "2026-01-05 12:00:00"},
                {"id": 2, "material": "PETG", "brand": "Bambu Lab", "created_at": "2026-01-05 12:00:00"},
            ]
        }
        usage = {
            "usage_history": [
                {"id": 9, "spool_id": 1, "grams_used": 12.5, "created_at": "2026-01-06 09:00:00"},
                {"id": 10, "spool_id": 2, "grams_used": 4.0, "created_at": "2026-01-06 10:00:00"},
                {"id": 11, "spool_id": 404, "grams_used": 1.0, "created_at": "2026-01-06 11:00:00"},
            ]
        }
        item_count, _ = await _service()._count_items(
            db_session, RestoreCategory.SPOOLS, {SPOOLS_PATH: spools, SPOOL_USAGE_PATH: usage}
        )

        tally = _CategoryTally()
        await _service()._restore_spools(db_session, spools, usage, False, tally, {})
        await db_session.commit()

        assert item_count == 5, "two spools plus three usage records, all of which the tally counts"
        assert tally.restored + tally.skipped + tally.failed == item_count

    @pytest.mark.asyncio
    async def test_preview_count_drops_by_one_when_the_local_credential_is_missing(self, db_session):
        parsed = {
            SETTINGS_PATH: {"settings": {"currency": "EUR", "prometheus_enabled": "true", "prometheus_token": "s3cret"}}
        }

        refused_count, refused_detail = await _service()._count_items(db_session, RestoreCategory.SETTINGS, parsed)

        db_session.add(Settings(key="prometheus_token", value="already-set"))
        await db_session.commit()
        allowed_count, allowed_detail = await _service()._count_items(db_session, RestoreCategory.SETTINGS, parsed)

        assert refused_count == allowed_count - 1
        assert refused_detail.code == "settingsCompanionWillSkip"
        assert refused_detail.params == {"count": 1, "companion": 1}
        # Nothing is being left off now, so the wording drops back to the plain
        # credential caveat.
        assert allowed_detail.code == "settingsCredentialsWillSkip"

    # --- The exposure class: a blank backup credential is the hole ----------
    #
    # The rule's second condition — "the backup carried a usable credential" —
    # is what stops it refusing an anonymous MQTT broker. It does not transfer to
    # Prometheus: a backup taken on an instance that enabled Prometheus without a
    # token (the field is optional and defaults to "") carries the toggle and no
    # usable token, and writing it opens /api/v1/metrics just as wide. That is
    # the *more* likely source of the exposure, not the less.

    @pytest.mark.asyncio
    async def test_prometheus_is_refused_when_the_backup_has_no_token_key_at_all(self, db_session):
        tally = await self._restore(db_session, currency="EUR", prometheus_enabled="true")

        rows = await self._rows(db_session)
        assert rows == {"currency": "EUR"}
        assert any("prometheus_enabled" in note and "switched off" in note for note in _messages(tally))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token", ["", "   "])
    async def test_prometheus_is_refused_when_the_backup_token_is_blank(self, db_session, token):
        tally = await self._restore(db_session, prometheus_enabled="true", prometheus_token=token)

        assert "prometheus_enabled" not in await self._rows(db_session)
        assert "settingsCompanionSkipped" in _codes(tally)

    @pytest.mark.asyncio
    async def test_the_preview_says_so_with_no_credential_key_to_skip(self, db_session):
        """The wording has to survive ``blocked`` being empty.

        The shared caveat counts credential-like keys *and* switches; on this
        payload there are no credential-like keys, so "0 credential-like key(s)
        will be skipped" would be noise.
        """
        parsed = {SETTINGS_PATH: {"settings": {"currency": "EUR", "prometheus_enabled": "true"}}}

        count, detail = await _service()._count_items(db_session, RestoreCategory.SETTINGS, parsed)

        assert count == 1
        assert detail.code == "settingsCompanionOnlyWillSkip"
        assert detail.params == {"companion": 1}

    @pytest.mark.asyncio
    async def test_the_availability_class_keeps_the_backup_credential_condition(self, db_session):
        """The other half of the same change: only Prometheus loses condition 2.

        Absent is treated like blank here — an anonymous broker is a working
        config, so refusing it would be a false positive.

        LDAP used to be in this list and is not any more: the same reasoning that
        makes an anonymous bind legitimate is what let an authored payload point
        the instance at another directory, so the family is refused outright
        rather than judged on availability. See
        ``test_the_whole_ldap_family_is_protected``.
        """
        await self._restore(db_session, mqtt_enabled="true", virtual_printer_enabled="true")

        rows = await self._rows(db_session)
        assert rows["mqtt_enabled"] == "true"
        assert rows["virtual_printer_enabled"] == "true"

    def test_every_exposure_toggle_is_a_companion_toggle(self):
        assert _COMPANION_EXPOSURE_TOGGLES.issubset(_COMPANION_CREDENTIALS)

    # --- Controls: over-refusal is the real risk here ----------------------

    @pytest.mark.asyncio
    async def test_a_usable_local_credential_lets_the_toggle_through(self, db_session):
        db_session.add(Settings(key="prometheus_token", value="already-set"))
        await db_session.commit()

        tally = await self._restore(db_session, prometheus_enabled="true", prometheus_token="s3cret")

        assert (await self._rows(db_session))["prometheus_enabled"] == "true"
        assert not any("switched off" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_the_exposure_route_still_stands_down_for_a_local_token(self, db_session):
        """Skipping condition 2 must not skip the local-state pass with it."""
        db_session.add(Settings(key="prometheus_token", value="already-set"))
        await db_session.commit()

        tally = await self._restore(db_session, prometheus_enabled="true")

        assert (await self._rows(db_session))["prometheus_enabled"] == "true"
        assert not any("switched off" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_the_exposure_route_still_stands_down_when_already_on(self, db_session):
        """The exposure pre-dates this restore either way — see ruling 3."""
        db_session.add(Settings(key="prometheus_enabled", value="true"))
        await db_session.commit()

        tally = await self._restore(db_session, overwrite=True, prometheus_enabled="true")

        assert (await self._rows(db_session))["prometheus_enabled"] == "true"
        assert not any("switched off" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_an_anonymous_broker_is_not_a_false_positive(self, db_session):
        """mqtt_relay passes an empty password straight through — a real config."""
        tally = await self._restore(db_session, mqtt_enabled="true", mqtt_broker="10.0.0.5")

        assert (await self._rows(db_session))["mqtt_enabled"] == "true"
        assert not any("switched off" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_a_blank_ldap_bind_password_no_longer_lets_the_toggle_through(self, db_session):
        """The inverted control, and the reason the LDAP pair had to go.

        A blank bind password used to read as "anonymous bind, a working config,
        do not over-refuse". It reads the same way to an attacker authoring the
        file, who wants no bind credential precisely because the directory is
        theirs — so the availability question cannot be asked about an
        authentication source at all.
        """
        await self._restore(db_session, ldap_enabled="true", ldap_bind_password="   ")

        assert "ldap_enabled" not in await self._rows(db_session)

    @pytest.mark.asyncio
    async def test_turning_a_toggle_off_is_always_written(self, db_session):
        await self._restore(db_session, prometheus_enabled="false", prometheus_token="s3cret")
        assert (await self._rows(db_session))["prometheus_enabled"] == "false"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["1", "on", "yes"])
    async def test_spellings_no_reader_treats_as_on_are_written(self, db_session, value):
        await self._restore(db_session, prometheus_enabled=value, prometheus_token="s3cret")
        assert (await self._rows(db_session))["prometheus_enabled"] == value

    @pytest.mark.asyncio
    async def test_ha_token_in_the_environment_counts_as_usable(self, db_session, monkeypatch):
        monkeypatch.setenv("HA_TOKEN", "from-env")
        await self._restore(db_session, ha_enabled="true", ha_token="s3cret")
        assert (await self._rows(db_session))["ha_enabled"] == "true"

    @pytest.mark.asyncio
    async def test_a_toggle_already_on_locally_is_written(self, db_session):
        """The exposure pre-dates the restore, so "left switched off" would be a lie."""
        db_session.add(Settings(key="prometheus_enabled", value="true"))
        await db_session.commit()

        tally = await self._restore(db_session, overwrite=True, prometheus_enabled="true", prometheus_token="s3cret")

        assert (await self._rows(db_session))["prometheus_enabled"] == "true"
        assert not any("switched off" in note for note in _messages(tally))

    # --- The map itself ----------------------------------------------------

    def test_every_companion_credential_is_blocked_and_no_toggle_is(self):
        """Guards the rule against a future edit to _SECRET_KEY_HINTS.

        If a credential stopped being blocked, its toggle would travel with it
        and the refusal would be pointless; if a toggle started being blocked,
        the pair would never be reached at all.
        """
        for toggle, credential in _COMPANION_CREDENTIALS.items():
            assert _is_blocked_setting_key(credential) is True, credential
            assert _is_blocked_setting_key(toggle) is False, toggle
            assert _is_protected_setting_key(toggle) is False, toggle

    def test_every_environment_override_names_a_companion_credential(self):
        assert set(_COMPANION_CREDENTIAL_ENV) <= set(_COMPANION_CREDENTIALS.values())

    @pytest.mark.asyncio
    async def test_plan_leaves_unusable_key_names_in_no_bucket(self, db_session):
        """They are the restore's ``failed``, not a refusal."""
        plan = await _service()._plan_settings(db_session, {"": "x", 7: "y", "currency": "EUR"})
        assert plan == _SettingsPlan()


class TestSpoolTagOverwrite:
    """Overwrite must not write the backup's *other* tag key onto a matched spool.

    ``tag_uid`` and ``tray_uuid`` are both in the overwrite ``setattr`` loop, and
    neither column has a unique constraint, so writing one onto a spool matched
    by the other silently creates a duplicate tag rather than erroring. After
    that ``_find_spool``'s ``.first()`` is non-deterministic and an AMS tag
    lookup resolves to an arbitrary one of the two. The same loop can also clear
    a tag the user has scanned since the backup was taken.
    """

    def _entry(self, **overrides):
        entry = {
            "id": 41,
            "material": "PLA",
            "brand": "Bambu Lab",
            "created_at": "2026-01-05 12:00:00",
            "tag_uid": "TAG-A",
            "tray_uuid": None,
        }
        entry.update(overrides)
        return entry

    async def _restore(self, db, entry, tally=None):
        tally = tally or _CategoryTally()
        await _service()._restore_spools(db, {"spools": [entry]}, None, True, tally, {})
        await db.commit()
        return tally

    @pytest.mark.asyncio
    async def test_an_empty_incoming_tag_does_not_clear_a_scanned_one(self, db_session):
        """The backup predates the scan, so the local tag is the newer fact."""
        db_session.add(Spool(material="PLA", brand="Bambu Lab", tag_uid="TAG-A", tray_uuid="TRAY-LIVE"))
        await db_session.commit()

        tally = await self._restore(db_session, self._entry(tray_uuid=None))

        row = (await db_session.execute(select(Spool))).scalar_one()
        assert row.tray_uuid == "TRAY-LIVE"
        assert any(note["code"] == "spoolTagKept" for note in tally.notes)

    @pytest.mark.asyncio
    async def test_a_tag_another_spool_already_holds_is_not_written(self, db_session):
        db_session.add(Spool(material="PLA", brand="Bambu Lab", tag_uid="TAG-A"))
        db_session.add(Spool(material="PETG", brand="Other", tray_uuid="TRAY-B"))
        await db_session.commit()

        tally = await self._restore(db_session, self._entry(tray_uuid="TRAY-B"))

        holders = (await db_session.execute(select(Spool).where(Spool.tray_uuid == "TRAY-B"))).scalars().all()
        assert len(holders) == 1, "a duplicate tray_uuid makes AMS lookups non-deterministic"
        assert holders[0].material == "PETG"
        assert any(note["code"] == "spoolTagKept" for note in tally.notes)

    @pytest.mark.asyncio
    async def test_the_note_counts_every_column_it_kept(self, db_session):
        db_session.add(Spool(material="PLA", brand="Bambu Lab", tag_uid="TAG-A", tray_uuid="TRAY-LIVE"))
        db_session.add(Spool(material="PETG", brand="Other", tag_uid="TAG-CLASH"))
        await db_session.commit()

        # Matched on tray_uuid, so the guard judges tag_uid: it clashes.
        tally = await self._restore(db_session, self._entry(tag_uid="TAG-CLASH", tray_uuid="TRAY-LIVE"))

        row = (await db_session.execute(select(Spool).where(Spool.tray_uuid == "TRAY-LIVE"))).scalar_one()
        assert row.tag_uid == "TAG-A"
        note = next(n for n in tally.notes if n["code"] == "spoolTagKept")
        assert note["params"] == {"count": 1}

    # --- Controls ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_a_free_tag_is_still_written(self, db_session):
        """The point of overwrite: a spool that gained a tray_uuid gets it."""
        db_session.add(Spool(material="PLA", brand="Bambu Lab", tag_uid="TAG-A"))
        await db_session.commit()

        tally = await self._restore(db_session, self._entry(tray_uuid="TRAY-NEW"))

        row = (await db_session.execute(select(Spool))).scalar_one()
        assert row.tray_uuid == "TRAY-NEW"
        assert not any(note["code"] == "spoolTagKept" for note in tally.notes)

    @pytest.mark.asyncio
    async def test_an_unchanged_tag_is_not_reported_as_kept(self, db_session):
        db_session.add(Spool(material="PLA", brand="Bambu Lab", tag_uid="TAG-A", tray_uuid="TRAY-A"))
        await db_session.commit()

        tally = await self._restore(db_session, self._entry(tray_uuid="TRAY-A"))

        assert not any(note["code"] == "spoolTagKept" for note in tally.notes)

    @pytest.mark.asyncio
    async def test_a_new_spool_keeps_both_tags_from_the_backup(self, db_session):
        """The guard is an overwrite-only concern; an insert is unaffected."""
        await self._restore(db_session, self._entry(tag_uid="TAG-NEW", tray_uuid="TRAY-NEW"))

        row = (await db_session.execute(select(Spool))).scalar_one()
        assert (row.tag_uid, row.tray_uuid) == ("TAG-NEW", "TRAY-NEW")

    @pytest.mark.asyncio
    async def test_find_spool_reports_which_key_matched(self, db_session):
        db_session.add(Spool(material="PLA", tag_uid="TAG-A"))
        db_session.add(Spool(material="PETG", tray_uuid="TRAY-B"))
        await db_session.commit()
        service = _service()

        assert (await service._find_spool(db_session, {"tag_uid": "TAG-A"}))[1] == "tag_uid"
        assert (await service._find_spool(db_session, {"tray_uuid": "TRAY-B"}))[1] == "tray_uuid"
        assert await service._find_spool(db_session, {"tag_uid": "NOPE"}) == (None, None)


class TestRestoreSpools:
    def _spool_entry(self, **overrides):
        entry = {
            "id": 41,
            "material": "PLA",
            "subtype": "Basic",
            "color_name": "Jade White",
            "brand": "Bambu Lab",
            "tag_uid": "AABBCCDD",
            "created_at": "2026-01-05 12:00:00",
            "weight_used": 120.5,
        }
        entry.update(overrides)
        return entry

    @pytest.mark.asyncio
    async def test_inserts_without_reusing_backup_id(self, db_session):
        """The backup's spool.id belongs to an unrelated row today."""
        db_session.add(Spool(material="PETG"))  # occupies id 1
        await db_session.commit()

        tally = _CategoryTally()
        payload = {"spools": [self._spool_entry(id=1)]}

        await _service()._restore_spools(db_session, payload, None, False, tally, {})
        await db_session.commit()

        spools = (await db_session.execute(select(Spool))).scalars().all()
        assert len(spools) == 2
        restored = next(s for s in spools if s.tag_uid == "AABBCCDD")
        assert restored.id != 1
        assert restored.material == "PLA"

    @pytest.mark.asyncio
    async def test_matches_existing_spool_by_tag_uid(self, db_session):
        db_session.add(Spool(material="PLA", tag_uid="AABBCCDD", color_name="Old"))
        await db_session.commit()
        tally = _CategoryTally()

        await _service()._restore_spools(db_session, {"spools": [self._spool_entry()]}, None, False, tally, {})
        await db_session.commit()

        assert len((await db_session.execute(select(Spool))).scalars().all()) == 1
        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_matches_existing_spool_by_tray_uuid(self, db_session):
        db_session.add(Spool(material="PLA", tray_uuid="1234" * 8))
        await db_session.commit()
        tally = _CategoryTally()
        entry = self._spool_entry(tag_uid=None, tray_uuid="1234" * 8)

        await _service()._restore_spools(db_session, {"spools": [entry]}, None, False, tally, {})
        await db_session.commit()

        assert len((await db_session.execute(select(Spool))).scalars().all()) == 1
        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_matches_tagless_spool_by_descriptive_composite(self, db_session):
        """Manually added spools have no tag, so fall back to created_at + description."""
        db_session.add(
            Spool(
                material="PLA",
                subtype="Basic",
                color_name="Jade White",
                brand="Bambu Lab",
                created_at=datetime(2026, 1, 5, 12, 0, 0),
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        entry = self._spool_entry(tag_uid=None)
        await _service()._restore_spools(db_session, {"spools": [entry]}, None, False, tally, {})
        await db_session.commit()

        assert len((await db_session.execute(select(Spool))).scalars().all()) == 1
        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_overwrite_updates_matched_spool(self, db_session):
        db_session.add(Spool(material="PLA", tag_uid="AABBCCDD", color_name="Old", weight_used=0))
        await db_session.commit()
        tally = _CategoryTally()

        await _service()._restore_spools(db_session, {"spools": [self._spool_entry()]}, None, True, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(Spool))).scalar_one()
        assert row.color_name == "Jade White"
        assert row.weight_used == 120.5
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_insert_preserves_created_at_so_repeat_restore_is_idempotent(self, db_session):
        """Second restore of the same backup must match, not duplicate."""
        service = _service()
        payload = {"spools": [self._spool_entry(tag_uid=None)]}

        await service._restore_spools(db_session, payload, None, False, _CategoryTally(), {})
        await db_session.commit()
        await service._restore_spools(db_session, payload, None, False, _CategoryTally(), {})
        await db_session.commit()

        spools = (await db_session.execute(select(Spool))).scalars().all()
        assert len(spools) == 1
        assert spools[0].created_at == datetime(2026, 1, 5, 12, 0, 0)

    @pytest.mark.asyncio
    async def test_usage_history_spool_id_is_remapped(self, db_session):
        """Usage rows must point at the new local spool id, not the backup's."""
        tally = _CategoryTally()
        inventory = {"spools": [self._spool_entry(id=41)]}
        usage = {
            "usage_history": [
                {
                    "id": 900,
                    "spool_id": 41,
                    "printer_id": None,
                    "print_name": "benchy.3mf",
                    "archive_id": None,
                    "weight_used": 12.0,
                    "percent_used": 5,
                    "status": "completed",
                    "created_at": "2026-02-01 09:00:00",
                }
            ]
        }

        await _service()._restore_spools(db_session, inventory, usage, False, tally, {})
        await db_session.commit()

        spool = (await db_session.execute(select(Spool))).scalar_one()
        row = (await db_session.execute(select(SpoolUsageHistory))).scalar_one()
        assert row.spool_id == spool.id
        assert row.print_name == "benchy.3mf"

    @pytest.mark.asyncio
    async def test_usage_history_archive_id_is_remapped(self, db_session):
        tally = _CategoryTally()
        inventory = {"spools": [self._spool_entry(id=41)]}
        usage = {
            "usage_history": [
                {
                    "spool_id": 41,
                    "archive_id": 77,
                    "weight_used": 1.0,
                    "created_at": "2026-02-01 09:00:00",
                }
            ]
        }
        archive = PrintArchive(filename="a.3mf", file_path="", file_size=1)
        db_session.add(archive)
        await db_session.flush()

        await _service()._restore_spools(db_session, inventory, usage, False, tally, {77: archive.id})
        await db_session.commit()

        row = (await db_session.execute(select(SpoolUsageHistory))).scalar_one()
        assert row.archive_id == archive.id

    @pytest.mark.asyncio
    async def test_usage_row_with_unresolvable_spool_is_skipped_and_explained(self, db_session):
        tally = _CategoryTally()
        usage = {"usage_history": [{"spool_id": 999, "weight_used": 1.0, "created_at": "2026-02-01 09:00:00"}]}

        await _service()._restore_spools(db_session, {"spools": []}, usage, False, tally, {})
        await db_session.commit()

        assert (await db_session.execute(select(SpoolUsageHistory))).scalars().first() is None
        assert tally.skipped == 1
        assert any("their spool is not in this backup's spool list" in note for note in _messages(tally))
        # No remedy is offered, because none exists: overwrite does not change
        # which spools land in the map (a skipped spool is mapped anyway), and
        # usage history is always restored alongside the spools category.
        assert not any("overwrite" in note.lower() for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_usage_resolves_against_a_spool_skipped_because_overwrite_is_off(self, db_session):
        """A skipped spool is still mapped, so its usage rows are not "unresolved".

        This is why the note above offers no remedy: turning overwrite on would
        not rescue anything, and saying so misdescribed which records are lost.
        """
        db_session.add(Spool(material="PLA", tag_uid="AABBCCDD", color_name="Old"))
        await db_session.commit()
        tally = _CategoryTally()
        inventory = {"spools": [self._spool_entry(id=41)]}
        usage = {
            "usage_history": [
                {"spool_id": 41, "print_name": "b.3mf", "weight_used": 5.0, "created_at": "2026-02-01 09:00:00"}
            ]
        }

        await _service()._restore_spools(db_session, inventory, usage, False, tally, {})
        await db_session.commit()

        spool = (await db_session.execute(select(Spool))).scalar_one()
        row = (await db_session.execute(select(SpoolUsageHistory))).scalar_one()
        assert row.spool_id == spool.id
        assert not any("spool list" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_usage_history_is_not_duplicated_on_repeat_restore(self, db_session):
        service = _service()
        inventory = {"spools": [self._spool_entry(id=41)]}
        usage = {
            "usage_history": [
                {"spool_id": 41, "print_name": "b.3mf", "weight_used": 5.0, "created_at": "2026-02-01 09:00:00"}
            ]
        }

        await service._restore_spools(db_session, inventory, usage, False, _CategoryTally(), {})
        await db_session.commit()
        await service._restore_spools(db_session, inventory, usage, False, _CategoryTally(), {})
        await db_session.commit()

        rows = (await db_session.execute(select(SpoolUsageHistory))).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_dropped_archive_link_is_explained(self, db_session):
        """Spools without archives nulls every usage -> archive link, silently."""
        tally = _CategoryTally()
        inventory = {"spools": [self._spool_entry(id=41)]}
        usage = {
            "usage_history": [
                {"spool_id": 41, "archive_id": 7, "weight_used": 1.0, "created_at": "2026-02-01 09:00:00"},
                {"spool_id": 41, "archive_id": 8, "weight_used": 2.0, "created_at": "2026-02-01 10:00:00"},
                {"spool_id": 41, "weight_used": 3.0, "created_at": "2026-02-01 11:00:00"},
            ]
        }

        # Empty archive_id_map: the archives category wasn't selected, so its
        # payload was never fetched and there is nothing to match against.
        await _service()._restore_spools(db_session, inventory, usage, False, tally, {})
        await db_session.commit()

        rows = (await db_session.execute(select(SpoolUsageHistory))).scalars().all()
        assert len(rows) == 3
        assert all(row.archive_id is None for row in rows)
        # Only the two that had a link to lose are counted.
        assert any("2 usage record(s) restored without their print-history link" in n for n in _messages(tally))
        assert any("select Print archives alongside" in n for n in _messages(tally))

    @pytest.mark.asyncio
    async def test_no_note_when_every_archive_link_resolves(self, db_session):
        tally = _CategoryTally()
        inventory = {"spools": [self._spool_entry(id=41)]}
        usage = {
            "usage_history": [
                {"spool_id": 41, "archive_id": 7, "weight_used": 1.0, "created_at": "2026-02-01 09:00:00"}
            ]
        }
        archive = PrintArchive(filename="linked.3mf", file_path="", file_size=1)
        db_session.add(archive)
        await db_session.flush()

        await _service()._restore_spools(db_session, inventory, usage, False, tally, {7: archive.id})
        await db_session.commit()

        row = (await db_session.execute(select(SpoolUsageHistory))).scalar_one()
        assert row.archive_id == archive.id
        assert not any("print-history link" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_dangling_printer_id_is_cleared(self, db_session):
        tally = _CategoryTally()
        inventory = {"spools": [self._spool_entry(id=41)]}
        usage = {
            "usage_history": [
                {"spool_id": 41, "printer_id": 4242, "weight_used": 1.0, "created_at": "2026-02-01 09:00:00"}
            ]
        }

        await _service()._restore_spools(db_session, inventory, usage, False, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(SpoolUsageHistory))).scalar_one()
        assert row.printer_id is None


class TestServerDefaultCreatedAtDedupe:
    """Dedupe against rows whose ``created_at`` came from the server default.

    Every test above seeds its "existing" row through the restore itself, which
    binds ``created_at`` explicitly — so both sides end up in SQLAlchemy's
    microsecond format and a SQL ``==`` matches. Rows the *application* created
    do not: SQLite fills ``server_default=func.now()`` from
    ``CURRENT_TIMESTAMP``, which has second precision, and the two strings
    never compare equal. That is the ordinary case — a user's own spools and
    their print history — and it duplicated the lot on every restore.
    """

    @staticmethod
    async def _native_spool(db_session, **kwargs):
        """A spool created the way the app creates one: no explicit created_at."""
        spool = Spool(material="PLA", brand="Bambu Lab", subtype="Basic", color_name="Jade White", **kwargs)
        db_session.add(spool)
        await db_session.commit()
        await db_session.refresh(spool)
        return spool

    def _entry_for(self, spool, **overrides):
        """The backup entry the collector writes for ``spool``."""
        entry = {
            "id": 41,
            "material": spool.material,
            "brand": spool.brand,
            "subtype": spool.subtype,
            "color_name": spool.color_name,
            "created_at": str(spool.created_at),
        }
        entry.update(overrides)
        return entry

    @pytest.mark.asyncio
    async def test_find_spool_matches_on_the_composite_fallback(self, db_session):
        spool = await self._native_spool(db_session)

        found, matched_on = await _service()._find_spool(db_session, self._entry_for(spool))

        assert found is not None and found.id == spool.id
        assert matched_on is None  # the composite, not a tag column

    @pytest.mark.asyncio
    async def test_a_tagless_spool_is_not_duplicated(self, db_session):
        spool = await self._native_spool(db_session)
        payload = {"spools": [self._entry_for(spool)]}
        tally = _CategoryTally()

        await _service()._restore_spools(db_session, payload, None, False, tally, {})
        await db_session.commit()

        assert len((await db_session.execute(select(Spool))).scalars().all()) == 1
        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_overwrite_updates_the_original_instead_of_inserting(self, db_session):
        spool = await self._native_spool(db_session)
        payload = {"spools": [self._entry_for(spool, weight_used=250.0)]}

        await _service()._restore_spools(db_session, payload, None, True, _CategoryTally(), {})
        await db_session.commit()

        row = (await db_session.execute(select(Spool))).scalar_one()
        assert row.id == spool.id
        assert row.weight_used == 250.0

    @pytest.mark.asyncio
    async def test_a_second_spool_added_later_stays_distinct(self, db_session):
        """The composite is only unique because of created_at, so the Python
        comparison has to stay exact — not a same-day tolerance."""
        spool = await self._native_spool(db_session)
        twin = Spool(material=spool.material, brand=spool.brand, subtype=spool.subtype, color_name=spool.color_name)
        twin.created_at = spool.created_at + timedelta(hours=1)
        db_session.add(twin)
        await db_session.commit()

        found, _ = await _service()._find_spool(db_session, self._entry_for(spool))

        assert found.id == spool.id

    @pytest.mark.asyncio
    async def test_existing_usage_history_is_not_re_inserted(self, db_session):
        spool = await self._native_spool(db_session, tag_uid="AABBCCDD")
        usage_row = SpoolUsageHistory(spool_id=spool.id, print_name="b.3mf", weight_used=5.0)
        db_session.add(usage_row)
        await db_session.commit()
        await db_session.refresh(usage_row)

        tally = _CategoryTally()
        inventory = {"spools": [self._entry_for(spool, tag_uid="AABBCCDD")]}
        usage = {
            "usage_history": [
                {
                    "spool_id": 41,
                    "print_name": "b.3mf",
                    "weight_used": 5.0,
                    "created_at": str(usage_row.created_at),
                }
            ]
        }

        await _service()._restore_spools(db_session, inventory, usage, False, tally, {})
        await db_session.commit()

        rows = (await db_session.execute(select(SpoolUsageHistory))).scalars().all()
        assert len(rows) == 1
        assert tally.skipped == 2  # the spool and its one usage row

    @pytest.mark.asyncio
    async def test_a_genuinely_new_usage_row_still_lands(self, db_session):
        """Dedupe by timestamp must not swallow a repeat of the same print."""
        spool = await self._native_spool(db_session, tag_uid="AABBCCDD")
        usage_row = SpoolUsageHistory(spool_id=spool.id, print_name="b.3mf", weight_used=5.0)
        db_session.add(usage_row)
        await db_session.commit()
        await db_session.refresh(usage_row)

        inventory = {"spools": [self._entry_for(spool, tag_uid="AABBCCDD")]}
        usage = {
            "usage_history": [
                {
                    "spool_id": 41,
                    "print_name": "b.3mf",
                    "weight_used": 5.0,
                    "created_at": str(usage_row.created_at + timedelta(days=1)),
                }
            ]
        }

        await _service()._restore_spools(db_session, inventory, usage, False, _CategoryTally(), {})
        await db_session.commit()

        assert len((await db_session.execute(select(SpoolUsageHistory))).scalars().all()) == 2


class TestRestoreArchives:
    def _archive_entry(self, **overrides):
        entry = {
            "id": 77,
            "filename": "benchy.3mf",
            "file_size": 2048,
            "content_hash": "abc123",
            "print_name": "Benchy",
            "status": "completed",
            "started_at": "2026-03-01 10:00:00",
            "completed_at": "2026-03-01 11:00:00",
            "created_at": "2026-03-01 10:00:00",
            "quantity": 1,
            "is_favorite": False,
        }
        entry.update(overrides)
        return entry

    @pytest.mark.asyncio
    async def test_inserts_metadata_only_row_with_empty_file_path(self, db_session):
        """print_archives.file_path is NOT NULL but is not in the backup."""
        tally = _CategoryTally()
        id_map: dict[int, int] = {}

        await _service()._restore_archives(db_session, {"archives": [self._archive_entry()]}, False, tally, id_map)
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.file_path == ""
        assert row.filename == "benchy.3mf"
        assert row.id != 77
        assert id_map == {77: row.id}
        assert any("metadata only" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_matches_existing_archive_by_hash_and_start(self, db_session):
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        await _service()._restore_archives(db_session, {"archives": [self._archive_entry()]}, False, tally, {})
        await db_session.commit()

        rows = (await db_session.execute(select(PrintArchive))).scalars().all()
        assert len(rows) == 1
        assert rows[0].file_path == "/data/benchy.3mf"
        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_filename_and_start_without_hash(self, db_session):
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                started_at=datetime(2026, 3, 1, 10, 0, 0),
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        entry = self._archive_entry(content_hash=None)
        await _service()._restore_archives(db_session, {"archives": [entry]}, False, tally, {})
        await db_session.commit()

        assert len((await db_session.execute(select(PrintArchive))).scalars().all()) == 1
        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_matches_archive_with_no_started_at_by_hash(self, db_session):
        """started_at is NULL for re-sliced archives, so it cannot be required.

        Gating both match branches on it meant these rows never matched: every
        restore re-inserted them and overwrite mode could never update them.
        """
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=None,
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        entry = self._archive_entry(started_at=None)
        await _service()._restore_archives(db_session, {"archives": [entry]}, False, tally, {})
        await db_session.commit()

        assert len((await db_session.execute(select(PrintArchive))).scalars().all()) == 1
        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_started_at_still_discriminates_when_present(self, db_session):
        """A NULL-tolerant match must not collapse rows that do differ."""
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        # Same file, no start time recorded — a different row, not that one.
        entry = self._archive_entry(started_at=None)
        await _service()._restore_archives(db_session, {"archives": [entry]}, False, tally, {})
        await db_session.commit()

        assert len((await db_session.execute(select(PrintArchive))).scalars().all()) == 2
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_soft_deleted_archive_is_not_restored_as_visible(self, db_session):
        """A backup keeps soft-deleted rows, so the flag has to survive.

        Their row is retained on purpose (stats keep counting the filament and
        energy), so without carrying deleted_at a restore turns an archive the
        user deleted back into a visible one.
        """
        tally = _CategoryTally()
        entry = self._archive_entry(deleted_at="2026-03-02 08:00:00")

        await _service()._restore_archives(db_session, {"archives": [entry]}, False, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.deleted_at == datetime(2026, 3, 2, 8, 0, 0)
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_locally_deleted_archive_stays_deleted_without_overwrite(self, db_session):
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                deleted_at=datetime(2026, 3, 5, 9, 0, 0),
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        # The backup predates the deletion, so its copy is live.
        await _service()._restore_archives(db_session, {"archives": [self._archive_entry()]}, False, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.deleted_at == datetime(2026, 3, 5, 9, 0, 0)
        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_overwrite_undeletes_a_locally_deleted_archive_and_says_so(self, db_session):
        """The entry has to *say* the archive was live — absent no longer means null.

        A commit taken before the collector wrote ``deleted_at`` carries no
        opinion about it, and overwrite now leaves the column alone in that
        case; see ``TestRestoredArchiveOwnership``.
        """
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                deleted_at=datetime(2026, 3, 5, 9, 0, 0),
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session, {"archives": [self._archive_entry(deleted_at=None)]}, True, tally, {}
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.deleted_at is None
        assert tally.restored == 1
        assert any("visible again" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_overwrite_updates_metadata_but_keeps_local_file_path(self, db_session):
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                notes="old",
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        entry = self._archive_entry(notes="restored note")
        await _service()._restore_archives(db_session, {"archives": [entry]}, True, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.notes == "restored note"
        # The 3MF on disk must not be orphaned by a metadata restore.
        assert row.file_path == "/data/benchy.3mf"
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_dangling_printer_and_project_links_are_cleared(self, db_session):
        tally = _CategoryTally()
        entry = self._archive_entry(printer_id=4242, project_id=4343)

        await _service()._restore_archives(db_session, {"archives": [entry]}, False, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.printer_id is None
        assert row.project_id is None
        assert any("no longer exist" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_valid_printer_link_is_preserved(self, db_session, printer_factory):
        printer = await printer_factory()
        tally = _CategoryTally()
        entry = self._archive_entry(printer_id=printer.id)

        await _service()._restore_archives(db_session, {"archives": [entry]}, False, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.printer_id == printer.id

    @pytest.mark.asyncio
    async def test_non_dict_entry_counts_as_failed(self, db_session):
        tally = _CategoryTally()
        await _service()._restore_archives(db_session, {"archives": ["nonsense"]}, False, tally, {})
        assert tally.failed == 1


class TestRestoreKprofiles:
    @staticmethod
    def _live(
        slot_id,
        filament_id="GFA00",
        name="Bambu PLA",
        setting_id="PFUS123",
        extruder_id=0,
        nozzle_id="HS00-0.4",
    ):
        """One profile as the printer currently reports it.

        ``extruder_id`` and ``nozzle_id`` mirror ``KProfile`` (bambu_mqtt.py),
        which has carried both all along; single-nozzle printers report
        extruder 0. Both are non-default fields there, so a live profile always
        has them — the double must too, or it licenses code that would break on
        the real object.
        """
        return SimpleNamespace(
            slot_id=slot_id,
            filament_id=filament_id,
            name=name,
            setting_id=setting_id,
            extruder_id=extruder_id,
            nozzle_id=nozzle_id,
        )

    def _client(self, live=None, sent="7", ack=(True, "")):
        """A connected printer client.

        ``set_kprofiles_batch`` returns the sequence_id it published under, not
        a success flag (#2718), and the verdict arrives separately from
        ``await_cali_ack`` as ``(ok, detail)``.
        """
        client = MagicMock()
        client.state.connected = True
        client.set_kprofiles_batch = MagicMock(return_value=sent)
        client.await_cali_ack = AsyncMock(return_value=ack)
        client.get_kprofiles = AsyncMock(return_value=list(live or []))
        return client

    def _payload(self, serial="00M09A123456789", nozzle="0.4"):
        return {
            f"kprofiles/{serial}/{nozzle}.json": {
                "version": "1.0",
                "printer_serial": serial,
                "nozzle_diameter": nozzle,
                "profiles": [
                    {
                        "slot_id": 0,
                        "name": "Bambu PLA",
                        "k_value": "0.020000",
                        "filament_id": "GFA00",
                        "nozzle_id": "HS00-0.4",
                        "extruder_id": 0,
                        "setting_id": "PFUS123",
                    }
                ],
            }
        }

    @pytest.mark.asyncio
    async def test_sends_batch_to_connected_printer(self, db_session, printer_factory):
        printer = await printer_factory(serial_number="00M09A123456789")
        client = self._client()
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        client.set_kprofiles_batch.assert_called_once()
        profiles, nozzle = client.set_kprofiles_batch.call_args.args
        assert nozzle == "0.4"
        assert profiles[0]["name"] == "Bambu PLA"
        assert profiles[0]["filament_id"] == "GFA00"
        assert tally.restored == 1
        assert manager.get_client.call_args.args == (printer.id,)

    @pytest.mark.asyncio
    async def test_always_warns_to_verify_on_the_printer(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        client = self._client()
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        # A refusal is now read and counted failed, so the caveat is narrowed to
        # what is genuinely left uncertain: a printer that never answers.
        assert any("verify the profiles on the printer" in note for note in _messages(tally))
        assert any("does not answer still counts as restored" in note for note in _messages(tally))
        assert not any("without acknowledgement" in note for note in _messages(tally))
        assert any("always overwrite" in note for note in _messages(tally))

    # --- cali_idx is resolved live, never taken from the backup -------------
    #
    # Regression cover for the silent no-op found testing on an X1E: the backup
    # stored cali_idx 8151, a Bambuddy edit re-keyed the profile to 4606, and
    # the restore aimed extrusion_cali_set at 8151. The printer dropped it and
    # the tally still said "1 restored".

    @pytest.mark.asyncio
    async def test_uses_the_live_cali_idx_not_the_backed_up_slot(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        payload["kprofiles/00M09A123456789/0.4.json"]["profiles"][0]["slot_id"] = 8151
        client = self._client(live=[self._live(slot_id=4606)])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        client.get_kprofiles.assert_awaited_once_with(nozzle_diameter="0.4")
        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == 4606, "must address the slot that exists now"
        assert profiles[0]["cali_idx"] != 8151, "must not reuse the backup's cali_idx"
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_matches_on_name_when_setting_id_was_regenerated(self, db_session, printer_factory):
        # A delete-then-add edit mints a fresh setting_id, so the name carries
        # the match instead.
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(live=[self._live(slot_id=4606, setting_id="PF9999999999")])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == 4606
        # The live setting_id wins: it is what the printer associates with the slot.
        assert profiles[0]["setting_id"] == "PF9999999999"

    @pytest.mark.asyncio
    async def test_unmatched_profile_is_added_rather_than_aimed_at_a_dead_slot(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(live=[])  # printer has nothing for this nozzle
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == -1, "-1 tells the printer to add a new profile"
        assert profiles[0]["setting_id"] == "PFUS123", "falls back to the backed-up preset"
        assert any("added as new profiles" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_different_filament_is_not_treated_as_a_match(self, db_session, printer_factory):
        # Same slot, different filament — matching on slot alone would clobber
        # an unrelated profile.
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(live=[self._live(slot_id=4606, filament_id="GFB99", name="Bambu PLA")])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == -1

    @pytest.mark.asyncio
    async def test_unreadable_live_index_degrades_to_adding(self, db_session, printer_factory):
        # A failed read must not abort the restore.
        await printer_factory(serial_number="00M09A123456789")
        client = self._client()
        client.get_kprofiles = AsyncMock(side_effect=RuntimeError("mqtt timeout"))
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == -1
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_sole_profile_for_a_filament_matches_without_setting_id_or_name(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        entry = payload["kprofiles/00M09A123456789/0.4.json"]["profiles"][0]
        entry["setting_id"] = None
        entry["name"] = ""
        client = self._client(live=[self._live(slot_id=4606, setting_id="PFOTHER", name="Renamed")])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == 4606

    @pytest.mark.asyncio
    async def test_ambiguous_filament_without_discriminator_is_added_not_guessed(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        entry = payload["kprofiles/00M09A123456789/0.4.json"]["profiles"][0]
        entry["setting_id"] = None
        entry["name"] = ""
        client = self._client(live=[self._live(slot_id=1, setting_id="A"), self._live(slot_id=2, setting_id="B")])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == -1, "two candidates and nothing to tell them apart"

    @pytest.mark.asyncio
    async def test_two_entries_cannot_claim_the_same_live_slot(self, db_session, printer_factory):
        """One live profile cannot stand in for two backed-up ones (#2656).

        Both entries fell through to the single-candidate arm, both took
        cali_idx 4606, both went into the batch — so the second overwrote the
        first on the printer while the tally counted two restored. Reachable
        whenever the user has deleted one of a pair since the backup, because
        the delete-then-add re-key is what strips the setting_id match.
        """
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        entries = payload["kprofiles/00M09A123456789/0.4.json"]["profiles"]
        entries[0].update(setting_id="PFGONE1", name="PLA Basic")
        entries.append({**entries[0], "setting_id": "PFGONE2", "name": "PLA Matte"})
        client = self._client(live=[self._live(slot_id=4606, setting_id="PFUS123", name="Bambu PLA")])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert [p["cali_idx"] for p in profiles] == [4606, -1], "the displaced entry has to be added, not aliased"
        assert sum(1 for p in profiles if p["cali_idx"] == 4606) == 1
        assert any("added as new profiles" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_the_displaced_entry_does_not_inherit_the_claimed_setting_id(self, db_session, printer_factory):
        """An add-as-new keeps its own preset, or it lands on top of the match anyway.

        cali_idx -1 is only safe if the rest of the payload doesn't point at the
        profile the first entry just claimed — the generated-setting_id fallback
        reads setting_id when cali_idx is -1.
        """
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        entries = payload["kprofiles/00M09A123456789/0.4.json"]["profiles"]
        entries[0].update(setting_id="PFGONE1", name="PLA Basic")
        entries.append({**entries[0], "setting_id": "PFGONE2", "name": "PLA Matte"})
        client = self._client(live=[self._live(slot_id=4606, setting_id="PFUS123", name="Bambu PLA")])

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, _CategoryTally())

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["setting_id"] == "PFUS123", "the match prefers the live preset"
        assert profiles[1]["setting_id"] == "PFGONE2", "the displaced entry keeps its own"

    @pytest.mark.asyncio
    async def test_two_entries_matching_two_live_profiles_keep_their_own_slots(self, db_session, printer_factory):
        """Control: the guard must not displace a legitimate second match."""
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        entries = payload["kprofiles/00M09A123456789/0.4.json"]["profiles"]
        entries.append({**entries[0], "setting_id": "PFUS456", "name": "Bambu PETG"})
        client = self._client(
            live=[
                self._live(slot_id=4606, setting_id="PFUS123", name="Bambu PLA"),
                self._live(slot_id=4607, setting_id="PFUS456", name="Bambu PETG"),
            ]
        )
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert [p["cali_idx"] for p in profiles] == [4606, 4607]
        assert not any("added as new profiles" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_a_claimed_slot_does_not_make_an_ambiguous_pair_matchable(self, db_session, printer_factory):
        """Two live profiles for one filament stay ambiguous after one is taken.

        The single-candidate fallback is judged against every candidate, not the
        unclaimed ones — otherwise claiming the first would leave exactly one
        "available" and turn a guess the code deliberately refuses into a match.
        """
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        entries = payload["kprofiles/00M09A123456789/0.4.json"]["profiles"]
        entries[0].update(setting_id="PFUS123", name="Bambu PLA")
        entries.append({**entries[0], "setting_id": None, "name": ""})
        client = self._client(
            live=[
                self._live(slot_id=1, setting_id="PFUS123", name="Bambu PLA"),
                self._live(slot_id=2, setting_id="PFOTHER", name="Renamed"),
            ]
        )

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, _CategoryTally())

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert [p["cali_idx"] for p in profiles] == [1, -1]

    # --- the match is scoped to the extruder it was calibrated on -----------
    #
    # get_kprofiles reads per nozzle *diameter*, so on a dual-nozzle printer
    # both extruders come back in one list. Scoping candidates on filament_id
    # alone let one extruder's calibration be written over the other's.

    @pytest.mark.asyncio
    async def test_each_extruders_profile_lands_on_its_own_extruder(self, db_session, printer_factory):
        """The same preset calibrated on both extruders of an H2D.

        Both live profiles share a filament_id *and* a setting_id, so the
        setting_id arm matched whichever the printer happened to list first —
        and with an entry per extruder the two swapped slots, each overwriting
        the other's calibration while the tally counted both restored.
        """
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        entries = payload["kprofiles/00M09A123456789/0.4.json"]["profiles"]
        entries.append({**entries[0], "extruder_id": 1, "nozzle_id": "HS00-0.4-R"})
        client = self._client(
            live=[
                # Right extruder first, which is what made the bug bite.
                self._live(slot_id=1001, extruder_id=1),
                self._live(slot_id=1000, extruder_id=0),
            ]
        )
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert [(p["extruder_id"], p["cali_idx"]) for p in profiles] == [(0, 1000), (1, 1001)]
        assert tally.restored == 2
        assert not any("added as new profiles" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_the_other_extruders_profile_is_not_a_candidate(self, db_session, printer_factory):
        """One backed-up entry, and the only live profile is the other extruder's.

        Adding as new is the right answer: extruder 0's calibration is not a
        stand-in for extruder 1's, however well the filament and preset line up.
        """
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(live=[self._live(slot_id=1001, extruder_id=1)])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == -1
        assert any("added as new profiles" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_an_entry_without_an_extruder_id_still_matches(self, db_session, printer_factory):
        """Control: a pre-#2656 backup carries no extruder_id.

        A missing key must leave the match exactly as it was, not turn every
        entry into an add.
        """
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        payload["kprofiles/00M09A123456789/0.4.json"]["profiles"][0].pop("extruder_id")
        client = self._client(live=[self._live(slot_id=4606)])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == 4606
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_a_live_index_that_reports_no_extruder_still_matches(self, db_session, printer_factory):
        """Control: the same, for a printer whose profiles carry no extruder_id."""
        await printer_factory(serial_number="00M09A123456789")
        live = SimpleNamespace(slot_id=4606, filament_id="GFA00", name="Bambu PLA", setting_id="PFUS123")
        client = self._client(live=[live])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["cali_idx"] == 4606
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_a_backup_without_a_nozzle_id_omits_the_key(self, db_session, printer_factory):
        """``set_kprofiles_batch`` defaults it, and only an absent key lets it.

        The default is ``p.get("nozzle_id", f"HS00-{diameter}")``, which a key
        present-and-None defeats — the batch would publish a null nozzle_id to
        the printer. Printers that omit the field (#1748) are the reason the
        default exists, so it has to be reachable.
        """
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        payload["kprofiles/00M09A123456789/0.4.json"]["profiles"][0].pop("nozzle_id")
        # No live match either, so neither source can supply one.
        client = self._client(live=[])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert "nozzle_id" not in profiles[0]

    @pytest.mark.asyncio
    async def test_the_backups_nozzle_id_is_used_when_nothing_is_live(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(live=[])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["nozzle_id"] == "HS00-0.4"

    @pytest.mark.asyncio
    async def test_the_live_nozzle_id_beats_the_backups(self, db_session, printer_factory):
        """The nozzle may have been swapped since the backup; we write to the
        one that is fitted now, exactly as with setting_id."""
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(live=[self._live(slot_id=4606, nozzle_id="SS00-0.4")])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["nozzle_id"] == "SS00-0.4"

    @pytest.mark.asyncio
    async def test_a_live_profile_without_a_nozzle_id_falls_back_to_the_backup(self, db_session, printer_factory):
        """Same defensive read as extruder_id: not every live profile carries
        every field."""
        await printer_factory(serial_number="00M09A123456789")
        live = SimpleNamespace(slot_id=4606, filament_id="GFA00", name="Bambu PLA", setting_id="PFUS123")
        client = self._client(live=[live])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        profiles, _ = client.set_kprofiles_batch.call_args.args
        assert profiles[0]["nozzle_id"] == "HS00-0.4"

    @pytest.mark.asyncio
    async def test_unknown_serial_is_skipped_with_reason(self, db_session):
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager"):
            await _service()._restore_kprofiles(db_session, self._payload(serial="NOSUCH"), tally)

        assert tally.restored == 0
        assert tally.skipped == 1
        assert any("No printer with serial NOSUCH" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_offline_printer_is_skipped_not_failed(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789", name="Shelf Printer")
        client = MagicMock()
        client.state.connected = False
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        assert tally.skipped == 1
        assert tally.failed == 0
        assert any("not connected" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_a_non_dict_profile_is_counted_failed_not_dropped(self, db_session, printer_factory):
        """The online path was the one place an entry left the tally entirely.

        ``_kprofile_profile_count`` counts it, so the offline and
        printer-missing paths already count the same entry skipped and the
        failure path counts it outstanding — only the connected path skipped it
        silently, so restored + skipped + failed came up short of the number the
        preview showed.
        """
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        path = next(iter(payload))
        payload[path]["profiles"] = [payload[path]["profiles"][0], "nonsense"]
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=self._client())
            await _service()._restore_kprofiles(db_session, payload, tally)

        assert tally.failed == 1
        assert tally.restored + tally.skipped + tally.failed == 2

    @pytest.mark.asyncio
    async def test_the_offline_path_counts_the_same_entry(self, db_session, printer_factory):
        """Control for the above: the two paths have to agree on the total."""
        await printer_factory(serial_number="00M09A123456789")
        payload = self._payload()
        path = next(iter(payload))
        payload[path]["profiles"] = [payload[path]["profiles"][0], "nonsense"]
        client = MagicMock()
        client.state.connected = False
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        assert tally.restored + tally.skipped + tally.failed == 2

    @pytest.mark.asyncio
    async def test_no_client_at_all_is_skipped(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=None)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        assert tally.skipped == 1

    @pytest.mark.asyncio
    async def test_publish_failure_counts_as_failed(self, db_session, printer_factory):
        # None is what set_kprofiles_batch returns when it could not publish —
        # a disconnected client. There is no ack to wait for in that case.
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(sent=None)
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        assert tally.failed == 1
        assert tally.restored == 0
        assert "kprofilesSendFailed" in _codes(tally)
        assert "kprofilesRefused" not in _codes(tally), "nothing was sent, so the printer refused nothing"
        client.await_cali_ack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publish_exception_is_contained(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        client = MagicMock()
        client.state.connected = True
        client.set_kprofiles_batch = MagicMock(side_effect=RuntimeError("mqtt down"))
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        assert tally.failed == 1

    # --- the printer's verdict decides the tally, not the publish ------------
    #
    # #2718 changed set_kprofiles_batch from returning a bool to returning the
    # sequence_id it published under. A sequence_id string is truthy, so a
    # restore that branches on the return value alone reports every refused
    # write as saved — the defect that fix closed in every other caller.

    @pytest.mark.asyncio
    async def test_awaits_the_ack_for_the_sequence_id_it_was_given(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(sent="4211")
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        client.await_cali_ack.assert_awaited_once_with("4211")
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_a_refused_batch_counts_failed_not_restored(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789", name="Shelf Printer")
        client = self._client(ack=(False, "invalid tray_id"))
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        assert tally.restored == 0
        assert tally.failed == 1
        assert "kprofilesRefused" in _codes(tally)
        assert "kprofilesSendFailed" not in _codes(tally), "it was sent — the printer answered no"
        note = next(n for n in tally.notes if n["code"] == "kprofilesRefused")
        assert note["params"]["reason"] == "invalid tray_id", "the printer's own reason has to survive"
        assert "Shelf Printer" in note["message"] and "invalid tray_id" in note["message"]

    @pytest.mark.asyncio
    async def test_a_silent_printer_still_counts_restored(self, db_session, printer_factory):
        # maziggy's rule, and await_cali_ack's own contract: no answer is not
        # evidence of refusal. Firmware that predates the ack never answers.
        await printer_factory(serial_number="00M09A123456789")
        client = self._client(ack=(True, "no acknowledgement from printer"))
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        assert tally.restored == 1
        assert tally.failed == 0
        assert "kprofilesRefused" not in _codes(tally)

    @pytest.mark.asyncio
    async def test_an_unreadable_ack_does_not_fail_the_batch(self, db_session, printer_factory):
        # Same situation one layer up: the write most likely landed, so this
        # degrades the way a timeout does rather than inventing a failure.
        await printer_factory(serial_number="00M09A123456789")
        client = self._client()
        client.await_cali_ack = AsyncMock(side_effect=RuntimeError("mqtt down"))
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(), tally)

        assert tally.restored == 1
        assert tally.failed == 0

    @pytest.mark.asyncio
    async def test_one_refused_nozzle_does_not_condemn_the_other(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        payload = {**self._payload(nozzle="0.4"), **self._payload(nozzle="0.8")}
        client = self._client()
        client.await_cali_ack = AsyncMock(side_effect=[(False, "busy"), (True, "")])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        assert tally.restored == 1
        assert tally.failed == 1

    @pytest.mark.asyncio
    async def test_each_nozzle_is_sent_separately(self, db_session, printer_factory):
        await printer_factory(serial_number="00M09A123456789")
        payload = {**self._payload(nozzle="0.4"), **self._payload(nozzle="0.8")}
        client = self._client()
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, payload, tally)

        assert client.set_kprofiles_batch.call_count == 2
        assert {c.args[1] for c in client.set_kprofiles_batch.call_args_list} == {"0.4", "0.8"}
        assert tally.restored == 2

    @pytest.mark.asyncio
    async def test_empty_payload_is_noted(self, db_session):
        tally = _CategoryTally()
        await _service()._restore_kprofiles(db_session, {}, tally)
        assert _codes(tally) == ["noData"]


class TestSoftDeletedArchiveRoundTrip:
    """The two halves of the soft-delete fix only work together.

    The collector keeps soft-deleted rows on purpose (their stats still count),
    so if it doesn't write ``deleted_at`` there is nothing for the restore to
    carry across and a deleted archive comes back visible. Covered end to end
    because each half looks harmless on its own.
    """

    @pytest.mark.asyncio
    async def test_deleted_at_survives_collect_then_restore(self, db_session):
        from backend.app.services.github_backup import github_backup_service

        deleted_at = datetime(2026, 3, 5, 9, 0, 0)
        db_session.add(
            PrintArchive(
                filename="trashed.3mf",
                file_path="",
                file_size=1024,
                content_hash="hash-trashed",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                deleted_at=deleted_at,
            )
        )
        await db_session.commit()

        files: dict = {}
        await github_backup_service._collect_archives(db_session, files)
        payload = files[ARCHIVES_PATH]
        assert payload["archives"][0]["deleted_at"] == str(deleted_at)

        # Restore that payload into an instance where the row is gone entirely.
        await db_session.execute(PrintArchive.__table__.delete())
        await db_session.commit()

        tally = _CategoryTally()
        await _service()._restore_archives(db_session, payload, False, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.deleted_at == deleted_at, "a deleted archive must not come back visible"


class TestRestoredArchiveOwnership:
    """A restored archive without an owner is invisible to the person who owns it.

    ``created_by_id`` is not attribution, it is the column the access check runs
    on: ``_ensure_archive_visible`` fails closed on NULL (404 for any caller
    without ``archives:read_all``) and the list paths filter
    ``created_by_id == user.id``. So on a multi-user instance the tally reported
    archives restored while their owner could neither list nor open them.
    """

    def _entry(self, **overrides):
        entry = {
            "id": 77,
            "filename": "benchy.3mf",
            "file_size": 2048,
            "content_hash": "abc123",
            "started_at": "2026-03-01 10:00:00",
            "created_at": "2026-03-01 10:00:00",
        }
        entry.update(overrides)
        return entry

    async def _user(self, db, username="alice"):
        user = User(username=username, role="operator")
        db.add(user)
        await db.flush()
        return user

    @pytest.mark.asyncio
    async def test_owner_is_carried_across(self, db_session):
        user = await self._user(db_session)
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_id=user.id)]}, False, tally, {}
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == user.id
        assert not any("owner cleared" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_an_unknown_owner_is_cleared_with_a_note_not_failed(self, db_session):
        """The archive is still worth having; an admin can reassign it."""
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_id=4242)]}, False, tally, {}
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id is None
        assert tally.restored == 1 and tally.failed == 0
        assert any("owner cleared" in note and "archives:read_all" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_the_owner_note_is_emitted_once_for_many_rows(self, db_session):
        tally = _CategoryTally()
        archives = [
            self._entry(id=1, content_hash="h1", filename="a.3mf", created_by_id=4242),
            self._entry(id=2, content_hash="h2", filename="b.3mf", created_by_id=4243),
        ]

        await _service()._restore_archives(db_session, {"archives": archives}, False, tally, {})
        await db_session.commit()

        assert sum(1 for note in _messages(tally) if "owner cleared" in note) == 1

    @pytest.mark.asyncio
    async def test_a_backup_without_the_key_still_restores_and_says_so(self, db_session):
        """Backups taken before the collector recorded it just can't know the owner.

        The archive is worth restoring anyway, but it lands ownerless — which is
        a 404 for everyone without ``archives:read_all``. Reporting N restored
        while the user who asked for them sees none is the failure mode the note
        exists to prevent.
        """
        tally = _CategoryTally()

        await _service()._restore_archives(db_session, {"archives": [self._entry()]}, False, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id is None
        assert tally.restored == 1
        assert not any("owner cleared" in note for note in _messages(tally))
        assert any("without an owner" in note and "archives:read_all" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_an_explicitly_ownerless_archive_is_reported_too(self, db_session):
        """Same consequence, so the same note: the source row had no owner either."""
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_id=None)]}, False, tally, {}
        )
        await db_session.commit()

        assert any("without an owner" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_a_stale_owner_is_not_reported_twice(self, db_session):
        """One row, one cause, one note — the cleared-owner branch already spoke."""
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_id=4242)]}, False, tally, {}
        )
        await db_session.commit()

        assert any("owner cleared" in note for note in _messages(tally))
        assert not any("without an owner" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_a_known_owner_is_not_reported(self, db_session):
        user = await self._user(db_session)
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_id=user.id)]}, False, tally, {}
        )
        await db_session.commit()

        assert not any("without an owner" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_the_unknown_owner_note_is_not_emitted_on_overwrite(self, db_session):
        """Overwrite keeps the local owner, so there is nothing to warn about."""
        bob = await self._user(db_session, "bob")
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=bob.id,
            )
        )
        await db_session.commit()
        tally = _CategoryTally()

        await _service()._restore_archives(db_session, {"archives": [self._entry()]}, True, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == bob.id
        assert not any("without an owner" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_overwrite_makes_the_local_owner_match_the_backup(self, db_session):
        alice = await self._user(db_session, "alice")
        bob = await self._user(db_session, "bob")
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=bob.id,
            )
        )
        await db_session.commit()

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_id=alice.id)]}, True, _CategoryTally(), {}
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == alice.id

    @pytest.mark.asyncio
    async def test_overwrite_leaves_the_owner_alone_when_the_backup_names_someone_unknown(self, db_session):
        """A name this instance cannot resolve is not an instruction to clear.

        Same epistemic state as the absent key below -- the backup has not told
        us who owns this archive -- so it takes the same action. Writing NULL
        instead inflicted the 404-for-its-own-owner failure on a local row that
        was fine, and on a rebuilt instance every user renamed since the backup
        took a whole archive history with them.
        """
        bob = await self._user(db_session, "bob")
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=bob.id,
            )
        )
        await db_session.commit()

        tally = _CategoryTally()
        await _service()._restore_archives(
            db_session,
            {"archives": [self._entry(created_by_id=4242, created_by_username="carol")]},
            True,
            tally,
            {},
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == bob.id, "an owner we cannot resolve must not displace one we can"
        assert tally.restored == 1
        # Nothing was taken away, so there is nothing to warn about -- the same
        # rule the absent-key case follows.
        assert not any("owner cleared" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_overwrite_leaves_the_owner_alone_when_the_backups_id_is_stale(self, db_session):
        """The pre-username fallback takes the rule too."""
        bob = await self._user(db_session, "bob")
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=bob.id,
            )
        )
        await db_session.commit()

        tally = _CategoryTally()
        await _service()._restore_archives(db_session, {"archives": [self._entry(created_by_id=4242)]}, True, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == bob.id
        assert not any("owner cleared" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_an_unresolvable_name_still_lands_ownerless_on_insert(self, db_session):
        """Control for the two above: with no local row there is nothing to keep.

        The archive is still restored -- it is worth having -- but it is
        invisible to everyone without archives:read_all, so it is said out loud.
        """
        await self._user(db_session, "alice")
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session,
            {"archives": [self._entry(created_by_id=4242, created_by_username="carol")]},
            False,
            tally,
            {},
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id is None
        assert tally.restored == 1 and tally.failed == 0
        assert any("does not have" in note and "archives:read_all" in note for note in _messages(tally))
        # One cause, one note -- the ownerless-insert note must not pile on.
        assert not any("does not record one" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_overwrite_leaves_the_owner_alone_when_the_backup_predates_the_key(self, db_session):
        """A pre-#2656 commit must not blank the owner of a row that was fine.

        The entry carries no ``created_by_id`` at all, so there is nothing to
        write. Treating that as an explicit null inflicted the exact bug the
        column was added to fix — a 404 for the owner — on rows the restore had
        no business touching, silently, while still counting them restored.
        """
        bob = await self._user(db_session, "bob")
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=bob.id,
            )
        )
        await db_session.commit()

        tally = _CategoryTally()
        await _service()._restore_archives(db_session, {"archives": [self._entry()]}, True, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == bob.id, "an old backup does not know the owner, so it must not clear one"
        assert tally.restored == 1

    @pytest.mark.asyncio
    async def test_overwrite_leaves_deleted_at_alone_when_the_backup_predates_the_key(self, db_session):
        """The mirror case: an old commit must not un-delete, and must not claim to.

        ``archivesUndeleted`` reads the same absent value, so the un-delete was
        not merely wrong but unannounced.
        """
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                deleted_at=datetime(2026, 3, 4, 8, 0, 0),
            )
        )
        await db_session.commit()

        tally = _CategoryTally()
        await _service()._restore_archives(db_session, {"archives": [self._entry()]}, True, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.deleted_at == datetime(2026, 3, 4, 8, 0, 0), "an old backup must not resurrect a deleted archive"
        assert not any("visible again" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_overwrite_still_clears_an_owner_the_backup_explicitly_nulls(self, db_session):
        """Control: absent is ignored, but an explicit null is still honoured.

        A current-format backup of an unowned archive has to be able to say so,
        or overwrite stops meaning "make the local row match the backup".
        """
        bob = await self._user(db_session, "bob")
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=bob.id,
            )
        )
        await db_session.commit()

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_id=None)]}, True, _CategoryTally(), {}
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id is None

    @pytest.mark.asyncio
    async def test_overwrite_still_undeletes_when_the_backup_explicitly_nulls(self, db_session):
        """Control for the deleted_at half, with the note that goes with it."""
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                deleted_at=datetime(2026, 3, 4, 8, 0, 0),
            )
        )
        await db_session.commit()

        tally = _CategoryTally()
        await _service()._restore_archives(db_session, {"archives": [self._entry(deleted_at=None)]}, True, tally, {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.deleted_at is None
        assert any("visible again" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_owner_survives_collect_then_restore(self, db_session):
        """Both halves, because each looks harmless alone.

        The collector never wrote the key, so there was nothing for the restore
        to carry across even once it wanted to.
        """
        from backend.app.services.github_backup import github_backup_service

        user = await self._user(db_session)
        db_session.add(
            PrintArchive(
                filename="owned.3mf",
                file_path="",
                file_size=1024,
                content_hash="hash-owned",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=user.id,
            )
        )
        await db_session.commit()

        files: dict = {}
        await github_backup_service._collect_archives(db_session, files)
        payload = files[ARCHIVES_PATH]
        assert payload["archives"][0]["created_by_id"] == user.id

        await db_session.execute(PrintArchive.__table__.delete())
        await db_session.commit()

        await _service()._restore_archives(db_session, payload, False, _CategoryTally(), {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == user.id, "a restored archive its owner cannot see is not restored"


class TestArchiveOwnerNaturalKey:
    """``created_by_username`` decides the owner; the id is only the fallback.

    Restoring onto a rebuilt instance is this feature's main use case, and the
    users table renumbers there. A raw ``created_by_id`` cannot tell a correct
    match from a live id that now belongs to somebody else, so the id path hands
    one person's print history to another under ``ARCHIVES_READ_OWN`` — silently,
    because ``archivesOwnerCleared`` only fires for an id that is *absent*.
    ``username`` is unique on ``users``, so resolving on it turns that silent
    misattribution into an ownerless row with a note.
    """

    def _entry(self, **overrides):
        entry = {
            "id": 77,
            "filename": "benchy.3mf",
            "file_size": 2048,
            "content_hash": "abc123",
            "started_at": "2026-03-01 10:00:00",
            "created_at": "2026-03-01 10:00:00",
        }
        entry.update(overrides)
        return entry

    async def _user(self, db, username):
        user = User(username=username, role="operator")
        db.add(user)
        await db.flush()
        return user

    @pytest.mark.asyncio
    async def test_the_name_resolves_across_a_renumbered_users_table(self, db_session):
        """The whole point: same person, different id, restore still finds them."""
        alice = await self._user(db_session, "alice")
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session,
            {"archives": [self._entry(created_by_id=alice.id + 500, created_by_username="alice")]},
            False,
            tally,
            {},
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == alice.id
        assert not any("owner cleared" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_the_name_beats_a_live_id_belonging_to_someone_else(self, db_session):
        """The misattribution case, and the one the id path cannot even detect.

        Both ids exist locally, so the id path would write bob's — a valid row,
        no note, alice's print history readable by bob.
        """
        alice = await self._user(db_session, "alice")
        bob = await self._user(db_session, "bob")

        await _service()._restore_archives(
            db_session,
            {"archives": [self._entry(created_by_id=bob.id, created_by_username="alice")]},
            False,
            _CategoryTally(),
            {},
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == alice.id, "the name is the natural key; the id is from another instance"

    @pytest.mark.asyncio
    async def test_a_renamed_owner_lands_ownerless_with_a_note(self, db_session):
        """No local match, so nothing to resolve — and the id is not a fallback here.

        Falling back to it is exactly the guess the name exists to prevent, so
        the row is cleared and said out loud instead.
        """
        bob = await self._user(db_session, "bob")
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session,
            {"archives": [self._entry(created_by_id=bob.id, created_by_username="alice")]},
            False,
            tally,
            {},
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id is None
        assert tally.restored == 1 and tally.failed == 0
        assert any("does not have" in note and "archives:read_all" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_the_unmatched_note_is_emitted_once_for_many_rows(self, db_session):
        tally = _CategoryTally()
        archives = [
            self._entry(id=1, content_hash="h1", filename="a.3mf", created_by_username="alice"),
            self._entry(id=2, content_hash="h2", filename="b.3mf", created_by_username="carol"),
        ]

        await _service()._restore_archives(db_session, {"archives": archives}, False, tally, {})
        await db_session.commit()

        assert sum(1 for note in _messages(tally) if "does not have" in note) == 1

    @pytest.mark.asyncio
    async def test_an_unmatched_name_does_not_also_claim_no_owner_was_recorded(self, db_session):
        """One row, one cause, one note — as with the stale-id branch."""
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_username="alice")]}, False, tally, {}
        )
        await db_session.commit()

        assert any("does not have" in note for note in _messages(tally))
        assert not any("without an owner" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_a_pre_username_backup_still_resolves_on_the_id(self, db_session):
        """The fallback has to keep working — every backup taken before this change."""
        alice = await self._user(db_session, "alice")

        await _service()._restore_archives(
            db_session, {"archives": [self._entry(created_by_id=alice.id)]}, False, _CategoryTally(), {}
        )
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == alice.id

    @pytest.mark.asyncio
    async def test_an_explicitly_ownerless_archive_reads_as_no_owner_not_as_unmatched(self, db_session):
        """A current-format backup of an unowned archive writes both keys null."""
        tally = _CategoryTally()

        await _service()._restore_archives(
            db_session,
            {"archives": [self._entry(created_by_id=None, created_by_username=None)]},
            False,
            tally,
            {},
        )
        await db_session.commit()

        assert any("without an owner" in note for note in _messages(tally))
        assert not any("does not have" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_overwrite_leaves_the_owner_alone_when_neither_key_is_present(self, db_session):
        """The absent-is-not-null rule still holds now that there are two keys."""
        bob = await self._user(db_session, "bob")
        db_session.add(
            PrintArchive(
                filename="benchy.3mf",
                file_path="/data/benchy.3mf",
                file_size=2048,
                content_hash="abc123",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=bob.id,
            )
        )
        await db_session.commit()

        await _service()._restore_archives(db_session, {"archives": [self._entry()]}, True, _CategoryTally(), {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == bob.id

    @pytest.mark.asyncio
    async def test_the_name_survives_collect_then_restore(self, db_session):
        """Both halves, because the collector writing nothing looks harmless alone."""
        from backend.app.services.github_backup import github_backup_service

        alice = await self._user(db_session, "alice")
        db_session.add(
            PrintArchive(
                filename="owned.3mf",
                file_path="",
                file_size=1024,
                content_hash="hash-owned",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
                created_by_id=alice.id,
            )
        )
        await db_session.commit()

        files: dict = {}
        await github_backup_service._collect_archives(db_session, files)
        payload = files[ARCHIVES_PATH]
        assert payload["archives"][0]["created_by_username"] == "alice"

        # Rebuilt instance: same person, and nothing else holds their old id.
        await db_session.execute(PrintArchive.__table__.delete())
        await db_session.execute(User.__table__.delete())
        await db_session.commit()
        rebuilt = await self._user(db_session, "alice")
        await db_session.commit()

        await _service()._restore_archives(db_session, payload, False, _CategoryTally(), {})
        await db_session.commit()

        row = (await db_session.execute(select(PrintArchive))).scalar_one()
        assert row.created_by_id == rebuilt.id

    @pytest.mark.asyncio
    async def test_the_collector_names_no_owner_for_an_unowned_archive(self, db_session):
        """Null rather than absent, so a restore can tell "none" from "not recorded"."""
        from backend.app.services.github_backup import github_backup_service

        db_session.add(
            PrintArchive(
                filename="unowned.3mf",
                file_path="",
                file_size=1024,
                content_hash="hash-unowned",
                started_at=datetime(2026, 3, 1, 10, 0, 0),
            )
        )
        await db_session.commit()

        files: dict = {}
        await github_backup_service._collect_archives(db_session, files)

        entry = files[ARCHIVES_PATH]["archives"][0]
        assert entry["created_by_username"] is None
        assert "created_by_username" in entry


class TestCategoryPathMapping:
    def setup_method(self):
        self.service = _service()
        self.available = [
            "backup_metadata.json",
            SETTINGS_PATH,
            SPOOLS_PATH,
            SPOOL_USAGE_PATH,
            ARCHIVES_PATH,
            "kprofiles/SERIAL1/0.4.json",
            "kprofiles/SERIAL1/0.8.json",
            "cloud_profiles/filament.json",
        ]

    def test_spools_includes_usage_history(self):
        paths = self.service._category_paths(RestoreCategory.SPOOLS, self.available)
        assert paths == [SPOOLS_PATH, SPOOL_USAGE_PATH]

    def test_kprofiles_globs_all_serials_and_nozzles(self):
        paths = self.service._category_paths(RestoreCategory.KPROFILES, self.available)
        assert paths == ["kprofiles/SERIAL1/0.4.json", "kprofiles/SERIAL1/0.8.json"]

    def test_absent_paths_are_omitted(self):
        paths = self.service._category_paths(RestoreCategory.SETTINGS, ["backup_metadata.json"])
        assert paths == []

    def test_cloud_profiles_are_not_a_restore_category(self):
        assert "cloud_profiles" not in {c.value for c in RestoreCategory}


class TestMutex:
    @pytest.mark.asyncio
    async def test_restore_refuses_while_a_backup_is_running(self):
        service = _service()
        with patch("backend.app.services.github_backup.github_backup_service") as backup:
            backup.is_running = True
            result = await service.run_restore(1, "HEAD", [RestoreCategory.SPOOLS])

        assert result["success"] is False
        assert "backup is currently running" in result["message"]

    @pytest.mark.asyncio
    async def test_restore_refuses_while_another_restore_is_running(self):
        service = _service()
        service._running_restore = True

        result = await service.run_restore(1, "HEAD", [RestoreCategory.SPOOLS])

        assert result["success"] is False
        assert "restore is already running" in result["message"]

    @pytest.mark.asyncio
    async def test_backup_refuses_while_a_restore_is_running(self):
        from backend.app.services.github_backup import GitHubBackupService

        backup_service = GitHubBackupService()
        with patch("backend.app.services.github_restore.github_restore_service") as restore:
            restore.is_running = True
            result = await backup_service.run_backup(1, trigger="manual")

        assert result["success"] is False
        assert "restore is currently running" in result["message"]


class TestMqttRelayReconfigure:
    """Restoring mqtt_* rows has to reach the live relay, not just the table."""

    @pytest.mark.asyncio
    async def test_reconfigures_from_the_committed_rows(self, db_session):
        db_session.add(Settings(key="mqtt_enabled", value="true"))
        db_session.add(Settings(key="mqtt_broker", value="restored.local"))
        db_session.add(Settings(key="mqtt_port", value="8883"))
        db_session.add(Settings(key="mqtt_use_tls", value="true"))
        # Never restorable (credential blocklist), so it comes from the row that
        # was already there.
        db_session.add(Settings(key="mqtt_password", value="kept"))
        await db_session.commit()
        tally = _CategoryTally()
        relay = MagicMock()
        relay.configure = AsyncMock(return_value=True)

        with patch("backend.app.services.mqtt_relay.mqtt_relay", relay):
            await _service()._reconfigure_mqtt_relay(db_session, {"mqtt_broker"}, tally)

        relay.configure.assert_awaited_once()
        sent = relay.configure.await_args.args[0]
        assert sent["mqtt_enabled"] is True
        assert sent["mqtt_broker"] == "restored.local"
        assert sent["mqtt_port"] == 8883
        assert sent["mqtt_use_tls"] is True
        assert sent["mqtt_password"] == "kept"
        assert sent["mqtt_topic_prefix"] == "bambuddy"
        assert tally.notes == []

    @pytest.mark.asyncio
    async def test_no_reconnect_when_no_mqtt_key_was_written(self, db_session):
        """configure() tears the connection down, so don't call it for a theme change."""
        tally = _CategoryTally()
        relay = MagicMock()
        relay.configure = AsyncMock()

        with patch("backend.app.services.mqtt_relay.mqtt_relay", relay):
            await _service()._reconfigure_mqtt_relay(db_session, {"currency", "theme"}, tally)

        relay.configure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broker_failure_is_noted_not_fatal(self, db_session):
        tally = _CategoryTally()
        relay = MagicMock()
        relay.configure = AsyncMock(side_effect=OSError("no route to broker"))

        with patch("backend.app.services.mqtt_relay.mqtt_relay", relay):
            await _service()._reconfigure_mqtt_relay(db_session, {"mqtt_enabled"}, tally)

        assert any("restart Bambuddy" in note for note in _messages(tally))

    @pytest.mark.asyncio
    async def test_restore_settings_reports_the_keys_it_wrote(self, db_session):
        db_session.add(Settings(key="mqtt_broker", value="old.local"))
        await db_session.commit()
        written: set[str] = set()
        payload = {
            "settings": {
                "mqtt_broker": "new.local",
                "currency": "EUR",
                "mqtt_password": "leaked",
                "auth_enabled": "false",
            }
        }

        await _service()._restore_settings(
            db_session, payload, overwrite=True, tally=_CategoryTally(), keys_written=written
        )

        # Skipped keys are not "written", or a blocked mqtt_password would
        # trigger a pointless reconnect.
        assert written == {"mqtt_broker", "currency"}

    @pytest.mark.asyncio
    async def test_keys_skipped_for_overwrite_off_are_not_reported(self, db_session):
        db_session.add(Settings(key="mqtt_broker", value="old.local"))
        await db_session.commit()
        written: set[str] = set()

        await _service()._restore_settings(
            db_session,
            {"settings": {"mqtt_broker": "new.local"}},
            overwrite=False,
            tally=_CategoryTally(),
            keys_written=written,
        )

        assert written == set()

    @pytest.mark.asyncio
    async def test_a_refused_mqtt_enabled_is_not_reported_as_written(self, db_session):
        """So the relay reconfigures from the *local* mqtt_enabled, not the backup's.

        The companion rule refuses ``mqtt_enabled`` when the backup's password
        cannot come across and there is none stored locally. It must not then
        appear in ``keys_written``, or _reconfigure_mqtt_relay would be asked to
        bring up a broker connection the restore deliberately declined to enable.
        """
        written: set[str] = set()

        await _service()._restore_settings(
            db_session,
            {"settings": {"mqtt_enabled": "true", "mqtt_password": "refused", "mqtt_broker": "new.local"}},
            overwrite=True,
            tally=_CategoryTally(),
            keys_written=written,
        )

        assert written == {"mqtt_broker"}


class TestApplyOrdering:
    """_apply must not hold SQLite's single writer any longer than one category.

    Two ways to overrun the 15 s busy_timeout, and the same fix closes both: the
    K-profile phase awaits an unresponsive printer (3 x 5 s per printer/nozzle),
    and a database category is one SELECT per row or per key against a few
    thousand archives plus a full usage history. Every concurrent writer in the
    app fails with "database is locked" while either runs.
    """

    def _recording_service(self, calls: list[str]):
        service = _service()
        # Sync side effects on purpose: an AsyncMock returns a coroutine its
        # side_effect hands back rather than awaiting it, so an async recorder
        # would never run.
        service._restore_archives = AsyncMock(side_effect=lambda *a, **k: calls.append("archives"))
        service._restore_spools = AsyncMock(side_effect=lambda *a, **k: calls.append("spools"))
        service._restore_settings = AsyncMock(side_effect=lambda *a, **k: calls.append("settings"))
        service._restore_kprofiles = AsyncMock(side_effect=lambda *a, **k: calls.append("kprofiles"))
        return service

    @pytest.mark.asyncio
    async def test_every_database_category_commits_before_the_next_one_starts(self):
        calls: list[str] = []
        service = self._recording_service(calls)
        db = MagicMock()
        db.commit = AsyncMock(side_effect=lambda: calls.append("commit"))

        await service._apply(
            db,
            {},
            [RestoreCategory.ARCHIVES, RestoreCategory.SPOOLS, RestoreCategory.SETTINGS],
            False,
        )

        assert calls == ["archives", "commit", "spools", "commit", "settings", "commit"]

    @pytest.mark.asyncio
    async def test_the_printer_phase_runs_with_no_write_transaction_open(self):
        """The K-profile phase is last, and everything before it is already committed."""
        calls: list[str] = []
        service = self._recording_service(calls)
        db = MagicMock()
        db.commit = AsyncMock(side_effect=lambda: calls.append("commit"))

        await service._apply(
            db,
            {},
            [RestoreCategory.ARCHIVES, RestoreCategory.SPOOLS, RestoreCategory.KPROFILES],
            False,
        )

        assert calls == ["archives", "commit", "spools", "commit", "kprofiles"]

    @pytest.mark.asyncio
    async def test_a_tally_is_recorded_only_after_its_category_commits(self):
        """What run_restore's failure path relies on to report honestly.

        A tally present in ``results`` has to mean "these rows are on disk". If
        the commit raises, the category must not appear — otherwise a failed
        restore reports rows that rolled back.
        """
        service = self._recording_service([])
        db = MagicMock()
        db.commit = AsyncMock(side_effect=RuntimeError("database is locked"))
        results: dict = {}

        with pytest.raises(RuntimeError):
            await service._apply(db, {}, [RestoreCategory.ARCHIVES], False, results=results)

        assert results == {}

    @pytest.mark.asyncio
    async def test_the_callers_results_dict_is_populated_in_place(self):
        """So a raise mid-run still leaves the committed categories visible."""
        service = self._recording_service([])
        db = MagicMock()
        db.commit = AsyncMock()
        service._restore_spools = AsyncMock(side_effect=RuntimeError("boom"))
        results: dict = {}

        with pytest.raises(RuntimeError):
            await service._apply(db, {}, [RestoreCategory.ARCHIVES, RestoreCategory.SPOOLS], False, results=results)

        assert set(results) == {"archives"}, "archives committed before spools ran; the caller must see it"


class TestKprofilePhaseFailure:
    """The K-profile phase runs after _apply has committed everything else.

    So an exception there used to reach run_restore's handler, which reports
    ``success: False`` with an empty ``results`` — over archive, spool and
    settings rows that are durable on disk. The honest-reporting theme of this
    feature inverted on exactly the path where it matters, and the post-commit
    MQTT reconfigure (downstream of the raise, inside the same try) was skipped,
    leaving the relay pointed at the pre-restore broker.
    """

    _SETTINGS = {"version": "1.0", "settings": {"mqtt_broker": "restored.local", "currency": "EUR"}}

    def _payload(self, profiles=None):
        return {
            SETTINGS_PATH: dict(self._SETTINGS),
            "kprofiles/00M09A123456789/0.4.json": {
                "profiles": [{"filament_id": "GFA00", "name": "Bambu PLA"}] if profiles is None else profiles
            },
        }

    def _session_patch(self, db_session):
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=db_session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return patch("backend.app.services.github_restore.async_session", return_value=cm)

    async def _configured_service(self, db_session, payload):
        from backend.app.models.github_backup import GitHubBackupConfig

        config = GitHubBackupConfig(repository_url="https://github.com/o/r", access_token="tok", provider="github")
        db_session.add(config)
        await db_session.commit()

        service = _service()
        service._resolve_ref = AsyncMock(return_value=("a" * 40, "", None))
        service._read_categories = AsyncMock(return_value=(payload, ""))
        return service, config.id

    @pytest.mark.asyncio
    async def test_the_committed_categories_are_still_reported(self, db_session):
        service = _service()
        service._restore_kprofiles = AsyncMock(side_effect=RuntimeError("mqtt exploded"))

        results = await service._apply(
            db_session,
            self._payload(),
            [RestoreCategory.SETTINGS, RestoreCategory.KPROFILES],
            False,
        )

        assert results[RestoreCategory.SETTINGS.value].restored == 2
        rows = {s.key: s.value for s in (await db_session.execute(select(Settings))).scalars().all()}
        assert rows == {"mqtt_broker": "restored.local", "currency": "EUR"}, "committed before the phase that failed"

    @pytest.mark.asyncio
    async def test_the_failure_is_counted_and_explained(self, db_session):
        service = _service()
        service._restore_kprofiles = AsyncMock(side_effect=RuntimeError("mqtt exploded"))

        results = await service._apply(
            db_session,
            self._payload(profiles=[{"filament_id": "GFA00"}, {"filament_id": "GFB99"}]),
            [RestoreCategory.SETTINGS, RestoreCategory.KPROFILES],
            False,
        )

        tally = results[RestoreCategory.KPROFILES.value]
        assert tally.failed == 2, "every profile the payload carried is unaccounted for"
        assert tally.restored == 0
        assert _codes(tally) == ["kprofilesStepFailed"]
        assert tally.notes[0]["params"]["reason"] == "mqtt exploded"

    @pytest.mark.asyncio
    async def test_the_relay_is_reconfigured_even_though_the_phase_failed(self, db_session):
        """The reconfigure sits downstream of the raise in run_restore's try."""
        service, config_id = await self._configured_service(db_session, self._payload())
        service._restore_kprofiles = AsyncMock(side_effect=RuntimeError("mqtt exploded"))
        relay = MagicMock()
        relay.configure = AsyncMock(return_value=True)

        with self._session_patch(db_session), patch("backend.app.services.mqtt_relay.mqtt_relay", relay):
            result = await service.run_restore(
                config_id, "a" * 40, [RestoreCategory.SETTINGS, RestoreCategory.KPROFILES]
            )

        assert result["success"] is True
        assert result["results"][RestoreCategory.SETTINGS.value]["restored"] == 2
        assert result["results"][RestoreCategory.KPROFILES.value]["failed"] == 1
        relay.configure.assert_awaited_once()
        assert relay.configure.await_args.args[0]["mqtt_broker"] == "restored.local"

    @pytest.mark.asyncio
    async def test_a_failure_before_the_commit_still_reports_nothing_restored(self, db_session):
        """Control: rolling back and saying so is right when nothing landed."""
        service, config_id = await self._configured_service(db_session, self._payload())
        service._restore_settings = AsyncMock(side_effect=RuntimeError("read failed"))
        relay = MagicMock()
        relay.configure = AsyncMock(return_value=True)

        with self._session_patch(db_session), patch("backend.app.services.mqtt_relay.mqtt_relay", relay):
            result = await service.run_restore(
                config_id, "a" * 40, [RestoreCategory.SETTINGS, RestoreCategory.KPROFILES]
            )

        assert result["success"] is False
        assert result["results"] == {}
        assert (await db_session.execute(select(Settings))).scalars().first() is None
        relay.configure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_later_category_failing_still_reports_the_earlier_one(self, db_session):
        """The database phase commits per category, so this is now reachable there too.

        Archives land and are committed; settings then raises. Reporting an empty
        result would be the same false "nothing was restored" the K-profile split
        already had to fix, over rows that are durable on disk.
        """
        service, config_id = await self._configured_service(
            db_session,
            {
                ARCHIVES_PATH: {
                    "version": "1.0",
                    "archives": [
                        {
                            "id": 1,
                            "filename": "benchy.3mf",
                            "content_hash": "hash-later",
                            "started_at": "2026-03-01 10:00:00",
                        }
                    ],
                },
                SETTINGS_PATH: dict(self._SETTINGS),
            },
        )
        service._restore_settings = AsyncMock(side_effect=RuntimeError("read failed"))

        with self._session_patch(db_session):
            result = await service.run_restore(
                config_id, "a" * 40, [RestoreCategory.ARCHIVES, RestoreCategory.SETTINGS]
            )

        assert result["success"] is False
        assert result["results"][RestoreCategory.ARCHIVES.value]["restored"] == 1
        assert RestoreCategory.SETTINGS.value not in result["results"], "settings rolled back; do not claim it"
        assert (await db_session.execute(select(PrintArchive))).scalars().first() is not None

    @pytest.mark.asyncio
    async def test_a_malformed_profiles_value_is_a_skipped_category_not_a_raise(self, db_session, printer_factory):
        """Belt-and-braces: the pre-loop count ran ahead of the per-call guards.

        ``sum(len(c.get("profiles") or []) ...)`` raises TypeError on a
        hand-edited or truncated backup whose ``profiles`` is not a list — and it
        raises after the database categories are already on disk.
        """
        await printer_factory(serial_number="00M09A123456789")
        client = MagicMock()
        client.state.connected = True
        client.set_kprofiles_batch = MagicMock(return_value="7")
        client.get_kprofiles = AsyncMock(return_value=[])
        tally = _CategoryTally()

        with patch("backend.app.services.github_restore.printer_manager") as manager:
            manager.get_client = MagicMock(return_value=client)
            await _service()._restore_kprofiles(db_session, self._payload(profiles=5), tally)

        client.set_kprofiles_batch.assert_not_called()
        assert (tally.restored, tally.failed) == (0, 0)
        assert "kprofilesStepFailed" not in _codes(tally)


class TestResolveRef:
    @pytest.mark.asyncio
    async def test_concrete_sha_passes_through_without_an_api_call(self):
        service = _service()
        service.list_commits = AsyncMock()
        config = MagicMock(branch="main")

        resolved, error, commit = await service._resolve_ref(config, "abc1234")

        assert resolved == "abc1234"
        assert error == ""
        # Nothing was fetched, so there is no entry to describe it with.
        assert commit is None
        service.list_commits.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_head_resolves_to_the_tip_sha(self):
        service = _service()
        service.list_commits = AsyncMock(
            return_value={"success": True, "commits": [{"sha": "tipsha1"}, {"sha": "older"}]}
        )
        config = MagicMock(branch="main")

        resolved, error, commit = await service._resolve_ref(config, "HEAD")

        assert resolved == "tipsha1"
        assert error == ""
        # Handed back so preview does not list commits a second time just to
        # describe the one it already fetched.
        assert commit == {"sha": "tipsha1"}

    @pytest.mark.asyncio
    async def test_empty_history_is_an_error(self):
        service = _service()
        service.list_commits = AsyncMock(return_value={"success": True, "commits": []})
        config = MagicMock(branch="main")

        resolved, error, commit = await service._resolve_ref(config, "HEAD")

        assert resolved is None
        assert "no commits" in error
        assert commit is None


class TestDescribeCommit:
    """A preview that says `commit: null` gives the user no idea what they picked."""

    def _config(self):
        return MagicMock(branch="main", provider="github", repository_url="https://github.com/o/r", access_token="t")

    def _entry(self, sha: str):
        return {"sha": sha, "message": "Bambuddy backup", "author": "Bambuddy", "date": "2026-07-01T10:00:00Z"}

    @pytest.mark.asyncio
    async def test_an_abbreviated_ref_matches_a_full_sha_in_the_window(self):
        """REF_PATTERN accepts 7 characters; providers return 40.

        The old exact `==` therefore never matched an abbreviated ref, even when
        the commit was right there in the top 20.
        """
        service = _service()
        full = "abc1234" + "0" * 33
        service.list_commits = AsyncMock(return_value={"success": True, "commits": [self._entry(full)]})

        found = await service._describe_commit(self._config(), "abc1234")

        assert found is not None
        assert found["sha"] == full

    @pytest.mark.asyncio
    async def test_a_full_sha_matches_an_abbreviated_entry(self):
        service = _service()
        service.list_commits = AsyncMock(return_value={"success": True, "commits": [self._entry("abc1234")]})

        found = await service._describe_commit(self._config(), "abc1234" + "0" * 33)

        assert found is not None

    @pytest.mark.asyncio
    async def test_a_commit_outside_the_window_is_fetched_directly(self):
        service = _service()
        service.list_commits = AsyncMock(return_value={"success": True, "commits": [self._entry("f" * 40)]})
        backend = MagicMock()
        backend.get_commit = AsyncMock(return_value={"success": True, "commit": self._entry("old" + "0" * 37)})

        with patch("backend.app.services.github_restore.get_provider_backend", return_value=backend):
            found = await service._describe_commit(self._config(), "old" + "0" * 37)

        assert found["sha"] == "old" + "0" * 37
        backend.get_commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_direct_lookup_failure_is_not_fatal(self):
        """It is a subject line: render the preview without it."""
        service = _service()
        service.list_commits = AsyncMock(return_value={"success": True, "commits": []})
        backend = MagicMock()
        backend.get_commit = AsyncMock(return_value={"success": False, "message": "boom", "commit": None})

        with patch("backend.app.services.github_restore.get_provider_backend", return_value=backend):
            assert await service._describe_commit(self._config(), "a" * 40) is None

    @pytest.mark.asyncio
    async def test_the_window_scan_is_not_run_twice(self):
        """_resolve_ref already listed commits for HEAD; preview reuses that."""
        service = _service()
        tip = self._entry("t" * 40)
        service.list_commits = AsyncMock(return_value={"success": True, "commits": [tip]})

        resolved, _, commit = await service._resolve_ref(self._config(), "HEAD")

        assert resolved == "t" * 40
        assert commit == tip
        assert service.list_commits.await_count == 1
