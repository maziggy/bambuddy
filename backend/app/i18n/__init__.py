"""Internationalization module for backend notifications."""

from typing import Any

# English translations
EN = {
    "notification": {
        # Print events
        "print_started": "Print Started",
        "print_completed": "Print Completed",
        "print_failed": "Print Failed",
        "print_stopped": "Print Stopped",
        "print_ended": "Print Ended",
        "print_progress": "Print {progress}% Complete",
        "estimated": "Estimated",
        "time": "Time",
        "filament": "Filament",
        "reason": "Reason",
        "unknown": "Unknown",

        # Printer events
        "printer_offline": "Printer Offline",
        "printer_disconnected": "{printer} has disconnected",
        "printer_error": "Printer Error: {error_type}",

        # Filament
        "filament_low": "Filament Low",
        "slot_at_percent": "{printer}: Slot {slot} at {percent}%",

        # Maintenance
        "maintenance_due": "Maintenance Due",
        "overdue": "OVERDUE",
        "soon": "Soon",

        # Test notification
        "test_title": "Bambuddy Test",
        "test_message": "This is a test notification from Bambuddy. If you see this, notifications are working correctly!",
    }
}

# German translations
DE = {
    "notification": {
        # Print events
        "print_started": "Druck gestartet",
        "print_completed": "Druck abgeschlossen",
        "print_failed": "Druck fehlgeschlagen",
        "print_stopped": "Druck gestoppt",
        "print_ended": "Druck beendet",
        "print_progress": "Druck {progress}% fertig",
        "estimated": "Geschätzt",
        "time": "Zeit",
        "filament": "Filament",
        "reason": "Grund",
        "unknown": "Unbekannt",

        # Printer events
        "printer_offline": "Drucker offline",
        "printer_disconnected": "{printer} wurde getrennt",
        "printer_error": "Druckerfehler: {error_type}",

        # Filament
        "filament_low": "Wenig Filament",
        "slot_at_percent": "{printer}: Slot {slot} bei {percent}%",

        # Maintenance
        "maintenance_due": "Wartung fällig",
        "overdue": "ÜBERFÄLLIG",
        "soon": "Bald",

        # Test notification
        "test_title": "Bambuddy Test",
        "test_message": "Dies ist eine Testbenachrichtigung von Bambuddy. Wenn Sie dies sehen, funktionieren die Benachrichtigungen!",
    }
}

# All available translations
TRANSLATIONS = {
    "en": EN,
    "de": DE,
}


def get_translation(lang: str, key: str, **kwargs: Any) -> str:
    """
    Get a translation string by key with optional interpolation.

    Args:
        lang: Language code (e.g., 'en', 'de')
        key: Dot-separated key path (e.g., 'notification.print_started')
        **kwargs: Values to interpolate into the string

    Returns:
        Translated string, or the key if not found
    """
    # Fall back to English if language not found
    translations = TRANSLATIONS.get(lang, TRANSLATIONS["en"])

    # Navigate to the nested key
    keys = key.split(".")
    value = translations
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            # Key not found, fall back to English
            value = TRANSLATIONS["en"]
            for k2 in keys:
                if isinstance(value, dict) and k2 in value:
                    value = value[k2]
                else:
                    return key  # Return key if not found in fallback either
            break

    if isinstance(value, str):
        # Interpolate values
        try:
            return value.format(**kwargs)
        except KeyError:
            return value

    return key


class Translator:
    """Helper class for translations with a specific language."""

    def __init__(self, lang: str = "en"):
        self.lang = lang if lang in TRANSLATIONS else "en"

    def t(self, key: str, **kwargs: Any) -> str:
        """Translate a key."""
        return get_translation(self.lang, key, **kwargs)
