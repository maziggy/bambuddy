"""Cloud-profile collection for Git backup (#2717).

The collector used to read a ``setting`` key the Bambu Cloud API never returns,
so ``cloud_profiles/*`` was never written while ``backup_metadata.json`` claimed
it was. It also asked for the auth-disabled credential store unconditionally,
which meant it saw no accounts at all once auth was on. These tests pin the
response shape it actually has to parse, the account enumeration, and the
metadata now telling the truth.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.settings import Settings
from backend.app.models.user import User
from backend.app.services.github_backup import GitHubBackupService

# The real listing body: keyed by preset type, each holding private/public
# lists. There is no top-level "setting" array, and the entries carry no "type"
# of their own — the type is the outer key, and Bambu calls process "print".
BAMBU_LISTING = {
    "filament": {
        "private": [
            {"setting_id": "PFUS1", "name": "My PLA", "version": "1.0", "user_id": "u-123"},
        ],
        "public": [
            {"setting_id": "GFSA00", "name": "Bambu PLA Basic", "version": "1.0"},
        ],
    },
    "printer": {
        "private": [{"setting_id": "PMUS1", "name": "My X1C", "version": "1.0"}],
        "public": [],
    },
    "print": {
        "private": [{"setting_id": "PSUS1", "name": "My 0.2mm", "version": "1.0"}],
        "public": [],
    },
}


def _detail(setting_id: str, name: str, base: str) -> dict:
    return {
        "setting_id": setting_id,
        "name": name,
        "type": "filament",
        "version": "1.0",
        "base_id": base,
        "filament_id": "P1234",
        "setting": {"filament_flow_ratio": ["0.98"]},
    }


def _bambu_cloud(listing=None, detail_side_effect=None):
    cloud = MagicMock()
    cloud.is_authenticated = True
    cloud.get_slicer_settings = AsyncMock(return_value=listing if listing is not None else BAMBU_LISTING)
    cloud.get_setting_detail = AsyncMock(
        side_effect=detail_side_effect or (lambda sid: _detail(sid, f"detail-{sid}", "GFSA00")),
    )
    cloud.close = AsyncMock()
    return cloud


def _orca_service(profiles):
    svc = MagicMock()
    svc.list_profiles = AsyncMock(return_value=profiles)
    svc.close = AsyncMock()
    return svc


@pytest.fixture
def service():
    return GitHubBackupService()


class TestCloudAccountEnumeration:
    """Which accounts a backup collects from."""

    @pytest.mark.asyncio
    async def test_auth_disabled_uses_the_global_store(self, service, db_session):
        """With auth off there is no User row at all — credentials live in the
        Settings table and the account is keyed ``global``."""
        with (
            patch(
                "backend.app.api.routes.cloud.get_stored_token",
                new_callable=AsyncMock,
                return_value=("bambu-token", "a@b.c", "global"),
            ),
            patch(
                "backend.app.api.routes.orca_cloud._load_credentials",
                new_callable=AsyncMock,
                return_value=MagicMock(token=None),
            ),
        ):
            bambu, orca = await service.cloud_accounts(db_session)

        assert bambu == [("global", None)]
        assert orca == []

    @pytest.mark.asyncio
    async def test_auth_enabled_finds_every_user_holding_a_token(self, service, db_session):
        """The bug that made this invisible: with auth on, tokens live on User
        rows, and the collector only ever looked at the global store. Each cloud
        is enumerated separately so a user connected to one shows up only there.
        """
        both = User(username="both", cloud_token="t1", orca_cloud_token="o1")
        bambu_only = User(username="bambu-only", cloud_token="t2")
        orca_only = User(username="orca-only", orca_cloud_token="o2")
        neither = User(username="neither")
        db_session.add_all([both, bambu_only, orca_only, neither])
        await db_session.commit()

        with (
            patch(
                "backend.app.api.routes.cloud.get_stored_token",
                new_callable=AsyncMock,
                return_value=(None, None, "global"),
            ),
            patch(
                "backend.app.api.routes.orca_cloud._load_credentials",
                new_callable=AsyncMock,
                return_value=MagicMock(token=None),
            ),
        ):
            bambu, orca = await service.cloud_accounts(db_session)

        assert sorted(key for key, _ in bambu) == [f"user-{both.id}", f"user-{bambu_only.id}"]
        assert sorted(key for key, _ in orca) == [f"user-{both.id}", f"user-{orca_only.id}"]

    @pytest.mark.asyncio
    async def test_global_and_per_user_accounts_coexist(self, service, db_session):
        """A Settings row survives someone enabling auth later. Dropping it
        would silently stop backing up that account's presets."""
        db_session.add(User(username="u", cloud_token="t1"))
        await db_session.commit()

        with (
            patch(
                "backend.app.api.routes.cloud.get_stored_token",
                new_callable=AsyncMock,
                return_value=("legacy-global", None, "global"),
            ),
            patch(
                "backend.app.api.routes.orca_cloud._load_credentials",
                new_callable=AsyncMock,
                return_value=MagicMock(token=None),
            ),
        ):
            bambu, _orca = await service.cloud_accounts(db_session)

        assert "global" in [key for key, _ in bambu]
        assert len(bambu) == 2


class TestBambuCollection:
    @pytest.mark.asyncio
    async def test_reads_the_shape_the_api_actually_returns(self, service, db_session):
        """The whole bug in one assertion: presets come out of
        ``data[type]["private"]``, not a flat ``setting`` list, and ``print``
        maps to ``process``."""
        files: dict = {}
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            new_callable=AsyncMock,
            return_value=_bambu_cloud(),
        ):
            counts = await service._collect_bambu_profiles(db_session, files, "global", None)

        assert counts == {"filament": 1, "printer": 1, "process": 1}
        assert set(files) == {
            "cloud_profiles/bambu/global/filament.json",
            "cloud_profiles/bambu/global/printer.json",
            "cloud_profiles/bambu/global/process.json",
        }

    @pytest.mark.asyncio
    async def test_public_presets_are_not_backed_up(self, service, db_session):
        """Bambu's bundled catalogue is identical for everyone, re-downloadable,
        and not recreatable under your account — backing it up would churn the
        repository on every run for no recovery value."""
        files: dict = {}
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            new_callable=AsyncMock,
            return_value=_bambu_cloud(),
        ):
            await service._collect_bambu_profiles(db_session, files, "global", None)

        filament = files["cloud_profiles/bambu/global/filament.json"]["profiles"]
        assert [p["setting_id"] for p in filament] == ["PFUS1"]

    @pytest.mark.asyncio
    async def test_stores_the_payload_a_restore_needs(self, service, db_session):
        """The listing is metadata only. Without ``base_id`` and ``setting``
        the backup is a list of names — ``create_setting`` cannot rebuild from
        it."""
        files: dict = {}
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            new_callable=AsyncMock,
            return_value=_bambu_cloud(),
        ):
            await service._collect_bambu_profiles(db_session, files, "global", None)

        preset = files["cloud_profiles/bambu/global/filament.json"]["profiles"][0]
        assert preset["base_id"] == "GFSA00"
        assert preset["setting"] == {"filament_flow_ratio": ["0.98"]}
        assert preset["type"] == "filament"

    @pytest.mark.asyncio
    async def test_account_identity_is_not_written_to_the_repo(self, service, db_session):
        """Backup repositories can be public, and ``user_id`` adds nothing to a
        rebuild."""
        files: dict = {}
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            new_callable=AsyncMock,
            return_value=_bambu_cloud(),
        ):
            await service._collect_bambu_profiles(db_session, files, "global", None)

        for payload in files.values():
            for preset in payload["profiles"]:
                assert "user_id" not in preset

    @pytest.mark.asyncio
    async def test_one_unreadable_preset_does_not_lose_the_others(self, service, db_session):
        """And it is counted, not swallowed — a partial backup that looks
        complete is how #2717 stayed invisible."""

        def detail(setting_id):
            if setting_id == "PFUS1":
                raise RuntimeError("boom")
            return _detail(setting_id, "ok", "GFSA00")

        files: dict = {}
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            new_callable=AsyncMock,
            return_value=_bambu_cloud(detail_side_effect=detail),
        ):
            counts = await service._collect_bambu_profiles(db_session, files, "global", None)

        assert "cloud_profiles/bambu/global/filament.json" not in files
        assert counts["printer"] == 1
        assert counts["process"] == 1
        assert counts["failed"] == 1

    @pytest.mark.asyncio
    async def test_unauthenticated_account_writes_nothing(self, service, db_session):
        files: dict = {}
        with patch(
            "backend.app.api.routes.cloud.build_authenticated_cloud",
            new_callable=AsyncMock,
            return_value=None,
        ):
            counts = await service._collect_bambu_profiles(db_session, files, "user-1", MagicMock())

        assert counts == {}
        assert files == {}


class TestOrcaCollection:
    @pytest.mark.asyncio
    async def test_groups_by_content_type_including_aliases(self, service, db_session):
        """Orca carries the type at ``content.type`` and uses BambuStudio-style
        aliases — ``machine`` is a printer, ``process`` and ``print`` are both
        process. Same map the Orca tab groups by."""
        profiles = [
            {"id": 1, "name": "f", "content": {"type": "filament"}},
            {"id": 2, "name": "m", "content": {"type": "machine"}},
            {"id": 3, "name": "p", "content": {"type": "print"}},
            {"id": 4, "name": "p2", "content": {"type": "process"}},
        ]
        files: dict = {}
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            new_callable=AsyncMock,
            return_value=_orca_service(profiles),
        ):
            counts = await service._collect_orca_profiles(db_session, files, "user-3", MagicMock())

        assert counts == {"filament": 1, "printer": 1, "process": 2}
        assert "cloud_profiles/orca/user-3/printer.json" in files

    @pytest.mark.asyncio
    async def test_content_is_stored_inline_without_a_second_fetch(self, service, db_session):
        """The sync-pull listing already carries each profile's content, so
        unlike Bambu there is no per-profile round trip."""
        svc = _orca_service([{"id": 7, "name": "f", "content": {"type": "filament", "flow": 0.98}}])
        files: dict = {}
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            new_callable=AsyncMock,
            return_value=svc,
        ):
            await service._collect_orca_profiles(db_session, files, "global", None)

        stored = files["cloud_profiles/orca/global/filament.json"]["profiles"][0]
        assert stored["content"] == {"type": "filament", "flow": 0.98}
        assert svc.list_profiles.await_count == 1

    @pytest.mark.asyncio
    async def test_unmapped_types_are_kept_not_dropped(self, service, db_session):
        """The Orca *route* drops profiles whose type it can't render, which is
        right for a list and wrong for a backup: silently omitting a profile
        because Orca added a type is the same class of bug as #2717."""
        profiles = [
            {"id": 1, "name": "f", "content": {"type": "filament"}},
            {"id": 2, "name": "x", "content": {"type": "something_new"}},
            {"id": 3, "name": "y", "content": {}},
        ]
        files: dict = {}
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            new_callable=AsyncMock,
            return_value=_orca_service(profiles),
        ):
            counts = await service._collect_orca_profiles(db_session, files, "global", None)

        assert counts["other"] == 2
        assert len(files["cloud_profiles/orca/global/other.json"]["profiles"]) == 2

    @pytest.mark.asyncio
    async def test_dead_pairing_writes_nothing_and_does_not_raise(self, service, db_session):
        """An unexpected failure building the Orca client must not abort the
        rest of the backup — the other accounts and the other cloud still have
        profiles worth collecting."""
        files: dict = {}
        with patch(
            "backend.app.api.routes.orca_cloud._build_authenticated_service",
            new_callable=AsyncMock,
            side_effect=RuntimeError("session expired"),
        ):
            counts = await service._collect_orca_profiles(db_session, files, "user-2", MagicMock())

        assert counts == {}
        assert files == {}

    @pytest.mark.asyncio
    async def test_the_backup_never_disconnects_an_account(self, service, db_session):
        """A backup is an observer. It must not change anyone's sign-in state
        on a schedule — least of all on Orca's composite rejection reason,
        which cannot tell a real revocation from a lost refresh-rotation race.
        The Profiles route clears the dead pairing instead, with the user
        present to act on it.
        """
        from fastapi import HTTPException

        build = AsyncMock(side_effect=HTTPException(status_code=401, detail="grant already used"))
        files: dict = {}
        with patch("backend.app.api.routes.orca_cloud._build_authenticated_service", build):
            counts = await service._collect_orca_profiles(db_session, files, "global", None)

        assert counts == {}
        assert build.await_args.kwargs["clear_on_auth_failure"] is False

    @pytest.mark.asyncio
    async def test_a_rejected_session_says_it_will_keep_being_skipped(self, service, db_session, caplog):
        """Not clearing means the warning recurs every run, so the one line the
        operator sees has to say how to stop it."""
        from fastapi import HTTPException

        files: dict = {}
        with (
            caplog.at_level("WARNING"),
            patch(
                "backend.app.api.routes.orca_cloud._build_authenticated_service",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=401, detail="refresh rejected: grant already used"),
            ),
        ):
            counts = await service._collect_orca_profiles(db_session, files, "global", None)

        assert counts == {}
        assert "paired again" in caplog.text
        assert "Later runs will skip it too" in caplog.text

    @pytest.mark.asyncio
    async def test_an_unreachable_orca_is_a_transient_skip(self, service, db_session, caplog):
        """502 is very likely gone by the next run, so it must not carry the
        "go and re-pair" advice a rejected session does."""
        from fastapi import HTTPException

        files: dict = {}
        with (
            caplog.at_level("WARNING"),
            patch(
                "backend.app.api.routes.orca_cloud._build_authenticated_service",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=502, detail="Orca Cloud unreachable: timeout"),
            ),
        ):
            counts = await service._collect_orca_profiles(db_session, files, "user-9", None)

        assert counts == {}
        assert "unreachable" in caplog.text
        assert "paired again" not in caplog.text


class TestCollectorAndMetadata:
    @pytest.mark.asyncio
    async def test_no_connected_account_collects_nothing(self, service, db_session):
        files: dict = {}
        with (
            patch(
                "backend.app.api.routes.cloud.get_stored_token",
                new_callable=AsyncMock,
                return_value=(None, None, "global"),
            ),
            patch(
                "backend.app.api.routes.orca_cloud._load_credentials",
                new_callable=AsyncMock,
                return_value=MagicMock(token=None),
            ),
        ):
            summary = await service._collect_cloud_profiles(db_session, files)

        assert summary == {"bambu": {}, "orca": {}}
        assert files == {}

    @pytest.mark.asyncio
    async def test_one_failing_account_does_not_stop_the_others(self, service, db_session):
        a = User(username="a", cloud_token="t1")
        b = User(username="b", cloud_token="t2")
        db_session.add_all([a, b])
        await db_session.commit()

        def build(db, user=None):
            if user is not None and user.username == "a":
                raise RuntimeError("cloud down for this account")
            return _bambu_cloud()

        files: dict = {}
        with (
            patch(
                "backend.app.api.routes.cloud.get_stored_token",
                new_callable=AsyncMock,
                return_value=(None, None, "global"),
            ),
            patch(
                "backend.app.api.routes.orca_cloud._load_credentials",
                new_callable=AsyncMock,
                return_value=MagicMock(token=None),
            ),
            patch(
                "backend.app.api.routes.cloud.build_authenticated_cloud",
                new_callable=AsyncMock,
                side_effect=build,
            ),
        ):
            summary = await service._collect_cloud_profiles(db_session, files)

        assert f"user-{a.id}" not in summary["bambu"]
        assert summary["bambu"][f"user-{b.id}"] == {"filament": 1, "printer": 1, "process": 1}

    @pytest.mark.asyncio
    async def test_metadata_reports_collection_not_configuration(self, service, db_session):
        """``contents.cloud_profiles`` said ``true`` on every backup, including
        the ones that wrote nothing. A restore has to be able to trust it."""
        config = MagicMock(
            backup_kprofiles=False,
            backup_cloud_profiles=True,
            backup_settings=False,
            backup_spools=False,
            backup_archives=False,
        )
        with patch.object(
            service, "_collect_cloud_profiles", new_callable=AsyncMock, return_value={"bambu": {}, "orca": {}}
        ):
            files = await service._collect_backup_data(db_session, config)

        assert files["backup_metadata.json"]["contents"]["cloud_profiles"] is False
        assert "cloud_profiles" not in files["backup_metadata.json"]

    @pytest.mark.asyncio
    async def test_metadata_records_per_account_counts_when_collected(self, service, db_session):
        config = MagicMock(
            backup_kprofiles=False,
            backup_cloud_profiles=True,
            backup_settings=False,
            backup_spools=False,
            backup_archives=False,
        )
        summary = {"bambu": {"user-3": {"filament": 2}}, "orca": {}}
        with patch.object(service, "_collect_cloud_profiles", new_callable=AsyncMock, return_value=summary):
            files = await service._collect_backup_data(db_session, config)

        assert files["backup_metadata.json"]["contents"]["cloud_profiles"] is True
        assert files["backup_metadata.json"]["cloud_profiles"] == summary


class TestSettingsFallbackIsStillHonoured:
    @pytest.mark.asyncio
    async def test_global_orca_row_is_discovered(self, service, db_session):
        """Orca's auth-disabled fallback lives in the same Settings table as
        Bambu's; both stores are read on every run."""
        db_session.add(Settings(key="orca_cloud_token", value="oc_ext_x"))
        await db_session.commit()

        with patch(
            "backend.app.api.routes.cloud.get_stored_token",
            new_callable=AsyncMock,
            return_value=(None, None, "global"),
        ):
            _bambu, orca = await service.cloud_accounts(db_session)

        assert orca == [("global", None)]
