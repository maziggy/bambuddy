"""What a notification says beyond its rendered text (#3641).

The template variables and the message text reach every channel. A channel that
can lay a message out, today Discord with its embed fields, gets the same facts
as a :class:`NotificationPresentation`, passed next to the text and never inside
``variables``, which the generic webhook forwards as it is.
"""

from dataclasses import dataclass

from backend.app.models.notification_template import DEFAULT_TEMPLATES

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
