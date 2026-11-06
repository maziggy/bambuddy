"""Old log lines naming a REST plug's URL come back with its host only.

Earlier versions logged the whole URL when switching or polling a REST plug,
from the plug service and from the HTTP client. The URL's path and query can be
the device's credential, in shapes no pattern recognises, so lines from those
two loggers are reduced to scheme and host when read.
"""

from backend.app.core.config import settings
from backend.app.services.log_reader import read_log_entries, sanitize_log_content

PLUG = (
    "2026-10-01 10:00:00,000 INFO [backend.app.services.rest_smart_plug] [x] "
    "Turned ON REST smart plug 'P' via GET http://plug.elsewhere.example:8080/relay/0?turn=on&auth_key=old-value-1"
)
CLIENT = (
    "2026-10-01 10:00:01,000 INFO [httpx] [x] "
    'HTTP Request: GET http://ha.elsewhere.example:8123/api/webhook/old-value-2 "HTTP/1.1 200 OK"'
)
LOGIN = (
    "2026-10-01 10:00:02,000 WARNING [backend.app.services.rest_smart_plug] [x] "
    "REST smart plug at http://admin:p?old-value-3@plug.elsewhere.example/x timed out"
)
OTHER = (
    "2026-10-01 10:00:03,000 INFO [backend.app.services.spoolman] [x] "
    "Fetched http://spoolman.elsewhere.example/api/v1/spool"
)
SECRETS = ("old-value-1", "old-value-2", "old-value-3")


def _write(tmp_path, monkeypatch):
    (tmp_path / "bambuddy.log").write_text("\n".join([PLUG, CLIENT, LOGIN, OTHER]) + "\n", encoding="utf-8")
    monkeypatch.setattr(settings, "log_dir", tmp_path)


def test_old_device_lines_come_back_with_the_host_only(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)

    entries, _total = read_log_entries(limit=50)

    text = "\n".join(e.message for e in entries)
    assert not [secret for secret in SECRETS if secret in text]
    assert "via GET http://plug.elsewhere.example:8080" in text
    assert "GET http://ha.elsewhere.example:8123 " in text
    assert "http://spoolman.elsewhere.example/api/v1/spool" in text


def test_a_search_cannot_confirm_a_device_secret(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)

    for secret in SECRETS:
        entries, _total = read_log_entries(limit=50, search=secret)
        assert entries == [], secret


def test_the_support_bundle_keeps_the_host_only():
    content = sanitize_log_content("\n".join([PLUG, CLIENT, LOGIN, OTHER]))

    assert not [secret for secret in SECRETS if secret in content]
    assert "spoolman.elsewhere.example/api/v1/spool" in content


QUOTED = (
    "2026-10-01 10:00:04,000 INFO [backend.app.services.rest_smart_plug] [x] "
    "Turned ON REST smart plug 'P' via GET http://plug.elsewhere.example/api/webhook/ab'old-value-4?auth_key=old-value-5"
)
QUOTED_LOGIN = (
    "2026-10-01 10:00:05,000 WARNING [backend.app.services.rest_smart_plug] [x] "
    "REST smart plug at http://admin:pa'old-value-6@plug.elsewhere.example/api/webhook/old-value-7 timed out"
)
QUOTED_SECRETS = ("old-value-4", "old-value-5", "old-value-6", "old-value-7")


def test_a_quote_inside_an_old_url_does_not_end_it(tmp_path, monkeypatch):
    (tmp_path / "bambuddy.log").write_text("\n".join([QUOTED, QUOTED_LOGIN]) + "\n", encoding="utf-8")
    monkeypatch.setattr(settings, "log_dir", tmp_path)

    entries, _total = read_log_entries(limit=50)
    bundle = sanitize_log_content("\n".join([QUOTED, QUOTED_LOGIN]))

    text = "\n".join(e.message for e in entries)
    assert not [secret for secret in QUOTED_SECRETS if secret in text]
    assert not [secret for secret in QUOTED_SECRETS if secret in bundle]
    assert "admin" not in text and "admin" not in bundle
    for secret in QUOTED_SECRETS:
        assert read_log_entries(limit=50, search=secret)[0] == [], secret


def test_a_known_value_inside_the_logger_name_does_not_unmask_the_line():
    content = sanitize_log_content("\n".join([PLUG, CLIENT]), {"smart": "[USER]", "httpx": "[USER]"})

    assert not [secret for secret in SECRETS if secret in content]


HEADER = (
    "2026-10-01 10:00:06,000 WARNING [backend.app.services.rest_smart_plug] [x] "
    "Failed to connect to REST smart plug at http://plug.elsewhere.example/x: "
    "Illegal header value b'Bearer old-value-8 '"
)
AT_AFTER_HOST = (
    "2026-10-01 10:00:07,000 WARNING [backend.app.services.rest_smart_plug] [x] "
    "REST smart plug at http://admin:pa@old-value-9?more@plug.elsewhere.example/x timed out"
)


def test_an_old_header_error_and_an_at_after_the_host_come_back_masked(tmp_path, monkeypatch):
    (tmp_path / "bambuddy.log").write_text("\n".join([HEADER, AT_AFTER_HOST]) + "\n", encoding="utf-8")
    monkeypatch.setattr(settings, "log_dir", tmp_path)

    text = "\n".join(e.message for e in read_log_entries(limit=50)[0])
    bundle = sanitize_log_content("\n".join([HEADER, AT_AFTER_HOST]))

    for secret in ("old-value-8", "old-value-9"):
        assert secret not in text and secret not in bundle, secret
        assert read_log_entries(limit=50, search=secret)[0] == [], secret


# Every message the REST plug service has logged in a shipped version, with a
# secret in every slot that held a URL, an error's text or raw input.
_PREFIX = "2026-10-01 10:00:{:02d},000 WARNING [backend.app.services.rest_smart_plug] [x] "
HISTORICAL = [
    "Unexpected error communicating with REST smart plug at http://plug.example/sec-01: odd sec-02",
    "Turned OFF REST smart plug 'P' via GET http://plug.example/relay?auth_key=sec-03",
    "Turned ON REST smart plug 'P' via GET http://plug.example/api/webhook/sec-04",
    "Blocked REST request to invalid URL: 192.168.1.5/api/webhook/sec-05",
    "Blocked REST request to invalid URL: admin:sec-06@192.168.1.5/relay",
    "Failed to connect to REST smart plug at http://plug.example/sec-07: cannot reach sec-08",
    "Failed to parse REST headers JSON; sending none",
    "Failed to parse REST headers JSON: Authorization: Bearer sec-09",
    "Failed to parse REST headers JSON: {'X-Api-Key': 'sec-10'}",
    "Failed to turn OFF REST smart plug 'P'",
    "Failed to turn ON REST smart plug 'P'",
    "No OFF URL configured for REST plug 'P'",
    "No ON URL configured for REST plug 'P'",
    "REST smart plug at http://plug.example/sec-11 returned error: Server error for url 'http://plug.example/sec-12' sec-13",
    "REST smart plug at http://plug.example/sec-14 timed out",
]
HISTORICAL_SECRETS = [f"sec-{n:02d}" for n in range(1, 15)]


def test_every_message_the_plug_service_ever_logged_comes_back_without_its_secrets(tmp_path, monkeypatch):
    lines = [_PREFIX.format(n) + message for n, message in enumerate(HISTORICAL)]
    (tmp_path / "bambuddy.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(settings, "log_dir", tmp_path)

    entries, _total = read_log_entries(limit=100)
    text = "\n".join(e.message for e in entries)
    bundle = sanitize_log_content("\n".join(lines))

    assert len(entries) == len(HISTORICAL)
    for secret in HISTORICAL_SECRETS:
        assert secret not in text, secret
        assert secret not in bundle, secret
        assert read_log_entries(limit=100, search=secret)[0] == [], secret
    assert "Turned ON REST smart plug 'P' via GET http://plug.example" in text


def test_a_refused_header_value_is_masked_from_any_logger(tmp_path, monkeypatch):
    """An HTTP client refusing a header quotes its value, which is commonly a
    token, whichever service sent it."""
    line = (
        "2026-10-01 10:00:30,000 WARNING [backend.app.services.homeassistant] [x] "
        "Failed to get HA entity state for switch.x: Illegal header value b'Bearer hdr-value-1 '"
    )
    name = (
        "2026-10-01 10:00:31,000 WARNING [backend.app.services.notifications] [x] "
        "Send failed: Illegal header name b'X-hdr-value-2'"
    )
    (tmp_path / "bambuddy.log").write_text(f"{line}\n{name}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "log_dir", tmp_path)

    text = "\n".join(e.message for e in read_log_entries(limit=50)[0])
    bundle = sanitize_log_content(f"{line}\n{name}")

    for secret in ("hdr-value-1", "hdr-value-2"):
        assert secret not in text and secret not in bundle, secret
        assert read_log_entries(limit=50, search=secret)[0] == [], secret
    assert "Failed to get HA entity state for switch.x: Illegal header value" in text
