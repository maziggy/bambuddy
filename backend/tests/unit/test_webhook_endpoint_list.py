"""The webhook endpoints listed in Settings are the ones that exist.

The "Webhook Endpoints" card on the API keys settings page is the only place
the UI tells an integrator what to call. It had drifted: it listed endpoints
that never existed (status of all printers, pause, resume), gave two wrong
paths, and left out start, cancel and the queue status. This compares the card
with the webhook router, both ways.
"""

import re
from pathlib import Path

from backend.app.api.routes.webhook import router
from backend.app.core.config import settings

SETTINGS_PAGE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages" / "SettingsPage.tsx"

# <span ...>POST</span>{' '}
# <span className="text-white">/api/v1/webhook/printer/:id/stop</span>
_LISTED = re.compile(
    r">(GET|POST|PUT|PATCH|DELETE)</span>\{' '\}\s*<span className=\"text-white\">(/api/v1/webhook/[^<\s]+)</span>"
)


def _listed() -> set[tuple[str, str]]:
    return {(method, path) for method, path in _LISTED.findall(SETTINGS_PAGE.read_text(encoding="utf-8"))}


def _routed() -> set[tuple[str, str]]:
    found = set()
    for route in router.routes:
        path = settings.api_prefix + re.sub(r"\{[^}]+\}", ":id", route.path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            found.add((method, path))
    return found


def test_every_webhook_path_on_the_page_is_read_by_this_test():
    """Otherwise an entry written in other markup would escape the comparison."""
    text = SETTINGS_PAGE.read_text(encoding="utf-8")
    occurrences = len(re.findall(r"/api/v1/webhook/", text))
    parsed = len(_LISTED.findall(text))
    assert occurrences == parsed, (
        f"{occurrences} webhook paths in SettingsPage.tsx but only {parsed} in the expected markup; "
        "write new entries like the existing ones (or extend _LISTED)"
    )


def test_the_settings_card_lists_exactly_the_webhook_routes():
    listed, routed = _listed(), _routed()
    assert listed, "no webhook endpoints found on the settings card; has its markup changed?"
    assert listed - routed == set(), f"listed in Settings but no such route: {sorted(listed - routed)}"
    assert routed - listed == set(), f"webhook route missing from the Settings card: {sorted(routed - listed)}"
