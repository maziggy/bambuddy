"""Write-only secrets: notification provider configs, settings, smart plugs.

Responses show a set secret as SECRET_PLACEHOLDER. A config sent back with
the placeholder keeps the stored value, an empty value clears it, and anything
else replaces it. A stored secret is only used for the destination it was
entered for: a config that changes the destination (or the provider type) must
carry the secret again, and a type change must bring a config of its own.
"""

from __future__ import annotations

from typing import Any

from backend.app.core.logging_filters import redact_url_credentials

SECRET_PLACEHOLDER = "********"

# The config fields that hold a credential, per provider type. For Discord and
# the generic webhook the URL itself is commonly the credential.
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "callmebot": ("apikey",),
    "ntfy": ("auth_token",),
    "pushover": ("user_key", "app_token"),
    "telegram": ("bot_token",),
    "email": ("password",),
    "discord": ("webhook_url",),
    "webhook": ("webhook_url", "auth_header"),
    "bark": ("device_key",),
    "gotify": ("app_token",),
    "notify": ("token",),
    "homeassistant": (),
}

# Every secret field name of every type. Responses mask these whatever the
# provider's type, so a key left over from another type is never shown.
ALL_SECRET_FIELDS = frozenset(field for fields in SECRET_FIELDS.values() for field in fields)

# The config fields that decide where the secrets are sent. Types not listed
# send to a fixed service.
DESTINATION_FIELDS: dict[str, tuple[str, ...]] = {
    "email": ("smtp_server", "smtp_port", "security"),
    "ntfy": ("server",),
    "bark": ("server",),
    "gotify": ("server",),
    "webhook": ("webhook_url",),
}


class SecretConfigError(ValueError):
    """A config that would reuse a stored secret where it may not."""


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    """The config as a response shows it: every set secret as the placeholder."""
    return {key: (SECRET_PLACEHOLDER if key in ALL_SECRET_FIELDS and value else value) for key, value in config.items()}


def same_value(a: Any, b: Any) -> bool:
    """Equal as the edit dialog sends them back: as text, None as empty.

    Not stripped: a sender compares the exact value (" starttls" is not TLS).
    """
    return ("" if a is None else str(a)) == ("" if b is None else str(b))


# What a sender uses for a destination field that is not in the config at all.
_SENDER_DEFAULTS: dict[tuple[str, str], str] = {
    ("email", "security"): "starttls",
}


def _effective(provider_type: str, config: dict[str, Any], field: str) -> Any:
    if field in config:
        return config[field]
    return _SENDER_DEFAULTS.get((provider_type, field))


def has_placeholder(config: dict[str, Any]) -> bool:
    return any(value == SECRET_PLACEHOLDER for value in config.values())


def reject_placeholder(config: dict[str, Any]) -> None:
    """A config with nothing stored behind it (a new provider, a test of one)."""
    if has_placeholder(config):
        raise SecretConfigError("Enter the secret values; nothing is stored for this provider yet")


def merge_config(
    stored_type: str,
    stored: dict[str, Any],
    new_type: str,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """The config to use: the placeholder replaced by the stored secret.

    Refused when a placeholder would carry a stored secret to another provider
    type or another destination.
    """
    kept = [key for key, value in incoming.items() if value == SECRET_PLACEHOLDER]
    if not kept:
        return dict(incoming)
    if new_type != stored_type:
        raise SecretConfigError(f"Enter {', '.join(kept)} again for the new provider type")
    unknown = [key for key in kept if key not in ALL_SECRET_FIELDS or not stored.get(key)]
    if unknown:
        raise SecretConfigError(f"Enter {', '.join(unknown)}; there is no stored value to keep")
    moved = [
        field
        for field in DESTINATION_FIELDS.get(stored_type, ())
        if incoming.get(field) != SECRET_PLACEHOLDER
        and not same_value(_effective(stored_type, incoming, field), _effective(stored_type, stored, field))
    ]
    if moved:
        raise SecretConfigError(f"Enter {', '.join(kept)} again: {', '.join(moved)} changed")
    return {key: (stored[key] if value == SECRET_PLACEHOLDER else value) for key, value in incoming.items()}


# Settings secrets and the settings that decide where each is sent.
SETTINGS_SECRETS: dict[str, tuple[str, ...]] = {
    "mqtt_password": ("mqtt_broker", "mqtt_port", "mqtt_use_tls"),
    "ha_token": ("ha_url",),
    "obico_ml_token": ("obico_ml_url",),
    "prometheus_token": (),
    "ldap_bind_password": ("ldap_server_url", "ldap_security"),
}

# Settings rows that hold a credential, by name: a Git backup writes them as
# the placeholder and a restore never writes them. By hint rather than by list,
# so a credential setting added later is covered by default.
SECRET_SETTING_KEY_HINTS = ("token", "secret", "password", "access_code", "api_key", "passphrase", "verifier")


def is_secret_setting_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in SECRET_SETTING_KEY_HINTS)


REST_PLUG_URLS = ("rest_on_url", "rest_off_url", "rest_status_url", "rest_power_url", "rest_energy_url")

# Slicer preset keys holding a printer connection's credentials (for a LAN
# printer, its access code). Slicer cloud sync carries them.
PRESET_SECRET_KEYS = ("printhost_apikey", "printhost_password", "printhost_user")


def _mask_url_logins(value: Any) -> Any:
    if isinstance(value, str):
        return redact_url_credentials(value)
    if isinstance(value, list):
        return [_mask_url_logins(item) for item in value]
    return value


def mask_preset(content: Any) -> Any:
    """A slicer preset with its connection credentials shown as the placeholder
    and the login in any of its URLs masked."""
    if not isinstance(content, dict):
        return content
    return {key: _mask_url_logins(value) for key, value in mask_fields(content, PRESET_SECRET_KEYS).items()}


# Smart plug secrets and the fields that decide where each is sent.
SMART_PLUG_SECRETS: dict[str, tuple[str, ...]] = {
    "password": ("plug_type", "ip_address"),
    "rest_headers": ("plug_type", *REST_PLUG_URLS),
}


def mask_fields(values: dict[str, Any], fields: Any) -> dict[str, Any]:
    """values with each set field of ``fields`` shown as the placeholder."""
    return {key: (SECRET_PLACEHOLDER if key in fields and value else value) for key, value in values.items()}


def merge_partial(
    stored: dict[str, Any],
    update: dict[str, Any],
    secrets: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    """A partial update with the placeholder resolved: the secret is kept.

    A secret is kept when it is sent as the placeholder or not sent at all.
    Refused when a kept secret's destination changes, or the placeholder
    stands for a secret that is not stored.
    """
    result = dict(update)
    for secret, destinations in secrets.items():
        sent = secret in update
        if sent and update[secret] == SECRET_PLACEHOLDER:
            if not stored.get(secret):
                raise SecretConfigError(f"Enter {secret}; there is no stored value to keep")
            del result[secret]
        elif sent:
            continue
        if not stored.get(secret):
            continue
        moved = [
            field for field in destinations if field in update and not same_value(update[field], stored.get(field))
        ]
        if moved:
            raise SecretConfigError(f"Enter {secret} again: {', '.join(moved)} changed")
    return result


# The SMTP settings that decide where the stored SMTP password is sent.
SMTP_DESTINATIONS = ("smtp_host", "smtp_port", "smtp_security")
