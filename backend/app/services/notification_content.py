"""What a notification says beyond its rendered text (#3641).

The template variables and the message text reach every channel. A channel that
can lay a message out, today Discord with its embed fields, gets the same facts
as a :class:`NotificationPresentation`, passed next to the text and never inside
``variables``, which the generic webhook forwards as it is.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from backend.app.models.notification_template import DEFAULT_TEMPLATES
from backend.app.utils.color_utils import color_emoji, color_family, color_note

# Discord's embed limits (https://discord.com/developers/docs/resources/message#embed-object-embed-limits)
DISCORD_MAX_FIELDS = 25
DISCORD_MAX_FIELD_NAME = 256
DISCORD_MAX_FIELD_VALUE = 1024
DISCORD_MAX_EMBED_TOTAL = 6000

# Bambu green: the embed's side colour when nothing more telling is known.
DEFAULT_EMBED_COLOR = 0x00AE42

_DEFAULTS_BY_EVENT = {t["event_type"]: (t["title_template"], t["body_template"]) for t in DEFAULT_TEMPLATES}


@dataclass(frozen=True)
class NotificationPresentation:
    """Fields and side colour for a channel that can show them (Discord's embed)."""

    fields: tuple[tuple[str, str], ...] = ()
    color: int | None = None


def is_unmodified_template(event_type: str, title_template: str, body_template: str) -> bool:
    """True when title and body are both the shipped text for the event.

    ``is_default`` on the row is no help: it is set when the row is seeded and an
    edit never clears it. Presentation only goes with the shipped wording, because
    its fields repeat what that wording says; an edited template has chosen its
    own and is sent exactly as written.
    """
    return _DEFAULTS_BY_EVENT.get(event_type) == (title_template, body_template)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def discord_fields(fields: tuple[tuple[str, str], ...], budget: int) -> list[dict]:
    """Embed fields within Discord's limits; *budget* is what is left of the embed's 6000 characters.

    A field with an empty name or value is dropped (Discord rejects the whole
    message for one), long values are cut, and fields that no longer fit are left out.
    """
    result: list[dict] = []
    for name, value in fields:
        if len(result) >= DISCORD_MAX_FIELDS:
            break
        name = _cut(name.strip(), DISCORD_MAX_FIELD_NAME)
        value = _cut(value.strip(), DISCORD_MAX_FIELD_VALUE)
        if not name or not value:
            continue
        if len(name) + len(value) > budget:
            break
        budget -= len(name) + len(value)
        result.append({"name": name, "value": value, "inline": False})
    return result


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _rgb_hex(color: str | None) -> str:
    """ "#RRGGBB" for a 6- or 8-character hex colour (``#`` optional), "" when it is neither or fully clear."""
    clean = (color or "").strip().removeprefix("#")
    if len(clean) not in (6, 8) or not set(clean) <= _HEX_DIGITS:
        return ""
    if color_family(clean) == "Clear":
        return ""
    return "#" + clean[:6].upper()


@dataclass(frozen=True)
class NotificationFilament:
    """One filament a notification is about: what the callers know, in structure.

    The text of a waiting reason stays as the scheduler words it; this carries the
    same filament as data, so the colour dot, the hex and the fields can be made
    from it. Every part may be missing.

    *slot* is the human label of the tray (the spelling filament-low alerts and the
    Inventory page use), *extra_colors* the other stops of a multi-colour spool.
    """

    type: str | None = None
    color_hex: str | None = None
    color_name: str | None = None
    slot: str | None = None
    extra_colors: str | None = None

    @property
    def hex(self) -> str:
        return _rgb_hex(self.color_hex)

    @property
    def emoji(self) -> str:
        return color_emoji(self.color_hex, self.extra_colors)

    @property
    def line(self) -> str:
        """ "\U0001f7e3 PLA Lavender Purple (#B39DDB)": dot, type, colour name, hex; what is known."""
        name = (self.color_name or "").strip()
        note = color_note(self.color_hex)
        if note and note not in name.lower():
            name = f"{name} {note}".strip()
        parts = [self.emoji, (self.type or "").strip(), name]
        if self.hex:
            parts.append(f"({self.hex})")
        return " ".join(p for p in parts if p)


def filament_variables(filaments: Iterable[NotificationFilament]) -> dict[str, str]:
    """The filament template variables for a list of filaments; every one is "" without any.

    Several filaments join in their order with ", " ("; " for ``{filament}``, whose
    entries have commas of their own). A filament that lacks a part leaves it out
    of that variable rather than leaving a gap.
    """
    items = list(filaments)

    def joined(values: Iterable[str], sep: str = ", ") -> str:
        return sep.join(v for v in values if v)

    return {
        "filament_type": joined((f.type or "").strip() for f in items),
        "color_name": joined((f.color_name or "").strip() for f in items),
        "color_hex": joined(f.hex for f in items),
        "color_emoji": joined(f.emoji for f in items),
        "slot": joined((f.slot or "").strip() for f in items),
        "filament": joined((f.line for f in items), "; "),
    }


def embed_color(filaments: Sequence[NotificationFilament]) -> int | None:
    """The embed's side colour: that of the one filament the event is about.

    None (the green) for several filaments, or one whose colour is unreadable,
    clear, or so light that a bar of it would vanish on Discord's light theme.
    """
    if len(filaments) != 1:
        return None
    rgb = filaments[0].hex
    if not rgb or color_family(rgb) == "White":
        return None
    return int(rgb[1:], 16)
