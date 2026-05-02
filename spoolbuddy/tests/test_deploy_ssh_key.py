"""Tests for daemon.main._deploy_ssh_key — Bambuddy key sync.

Background: Bambuddy generates an ed25519 keypair under its data dir and ships
the public half to the SpoolBuddy daemon over the registration/heartbeat
response. The daemon writes that key into ~/.ssh/authorized_keys so Bambuddy
can SSH in to drive remote updates. Whenever Bambuddy's keypair rotates (data
volume wiped, container recreated, fresh deploy) the device's authorized_keys
must drop the old entries and pick up the new one — otherwise:

  1. SSH updates start failing silently with permission-denied
  2. Stale Bambuddy-tagged keys pile up over time, eroding the security
     boundary (any prior keypair Bambuddy held is permanently authorized).

These tests pin the replace-not-append semantics of the deploy helper.
"""

from unittest.mock import patch

from daemon.main import _deploy_ssh_key

CURRENT_KEY = "ssh-ed25519 AAAACURRENT bambuddy-spoolbuddy"
STALE_KEY_1 = "ssh-ed25519 AAAASTALE1 bambuddy-spoolbuddy"
STALE_KEY_2 = "ssh-ed25519 AAAASTALE2 bambuddy-spoolbuddy"
USER_KEY = "ssh-ed25519 AAAAUSER alice@laptop"


class TestDeploySshKey:
    def test_creates_authorized_keys_when_missing(self, tmp_path):
        with patch("daemon.main.Path.home", return_value=tmp_path):
            _deploy_ssh_key(CURRENT_KEY)

        auth_keys = tmp_path / ".ssh" / "authorized_keys"
        assert auth_keys.exists()
        assert auth_keys.read_text().strip() == CURRENT_KEY
        assert auth_keys.stat().st_mode & 0o777 == 0o600

    def test_replaces_all_prior_bambuddy_tagged_keys(self, tmp_path):
        """The pile-up scenario: 6+ stale keys accumulated over rotations.
        After deploy, only the current key remains — no growth."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        auth_keys = ssh_dir / "authorized_keys"
        auth_keys.write_text(f"{STALE_KEY_1}\n{STALE_KEY_2}\n")

        with patch("daemon.main.Path.home", return_value=tmp_path):
            _deploy_ssh_key(CURRENT_KEY)

        lines = auth_keys.read_text().strip().splitlines()
        assert lines == [CURRENT_KEY]

    def test_preserves_unrelated_user_keys(self, tmp_path):
        """Only Bambuddy-tagged keys get replaced — user's own keys stay."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        auth_keys = ssh_dir / "authorized_keys"
        auth_keys.write_text(f"{USER_KEY}\n{STALE_KEY_1}\n")

        with patch("daemon.main.Path.home", return_value=tmp_path):
            _deploy_ssh_key(CURRENT_KEY)

        lines = auth_keys.read_text().strip().splitlines()
        assert USER_KEY in lines
        assert STALE_KEY_1 not in lines
        assert CURRENT_KEY in lines

    def test_idempotent_when_already_in_sync(self, tmp_path):
        """No-op when authorized_keys already matches the desired state —
        avoids needless writes on every heartbeat."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        auth_keys = ssh_dir / "authorized_keys"
        auth_keys.write_text(f"{USER_KEY}\n{CURRENT_KEY}\n")
        original_mtime = auth_keys.stat().st_mtime_ns

        with patch("daemon.main.Path.home", return_value=tmp_path):
            _deploy_ssh_key(CURRENT_KEY)

        assert auth_keys.stat().st_mtime_ns == original_mtime

    def test_swallows_write_errors(self, tmp_path):
        """A failed deploy must not crash the heartbeat loop."""
        with (
            patch("daemon.main.Path.home", return_value=tmp_path),
            patch("daemon.main.Path.mkdir", side_effect=PermissionError("readonly fs")),
        ):
            _deploy_ssh_key(CURRENT_KEY)  # should not raise
