"""Shared helpers for normalizing RFID tag and tray identifiers."""

import uuid


def normalize_hex(value: str | None) -> str:
    if not value:
        return ""
    hex_chars = "".join(ch for ch in str(value).strip() if ch in "0123456789abcdefABCDEF")
    return hex_chars.upper()


def normalize_tag_uid(value: str | None) -> str:
    uid = normalize_hex(value)
    # DB column is VARCHAR(16), so keep the least-significant bytes if longer.
    if len(uid) > 16:
        uid = uid[-16:]
    return uid


def normalize_tray_uuid(value: str | None) -> str:
    uuid = normalize_hex(value)
    # DB column is VARCHAR(32). Keep canonical 32-char UUID when possible.
    if len(uuid) >= 32:
        uuid = uuid[:32]
    return uuid


def is_bambu_tray_uuid(value: str | None) -> bool:
    """True when ``value`` is a tray UUID a Bambu tag really carries.

    Every tray_uuid seen from the AMS and from tag block 9 is an RFC 4122
    version-4 UUID. SpoolBuddy daemons before #984 sent tag blocks 4-5 instead,
    which hold the filament type ("PLA Matte" as hex) and fail this check, so
    every spool of one type would share an id if such a value were matched or
    stored.
    """
    normalized = normalize_tray_uuid(value)
    if len(normalized) != 32:
        return False
    parsed = uuid.UUID(hex=normalized)
    return parsed.version == 4 and parsed.variant == uuid.RFC_4122
