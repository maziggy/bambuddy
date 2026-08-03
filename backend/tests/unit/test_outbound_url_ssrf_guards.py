"""Outbound-URL SSRF policy: two tiers, applied consistently.

Bambuddy makes outbound HTTP requests to hosts the operator configures. Which
policy applies is a property of the *service*, not the caller:

- LAN-service (Spoolman, ntfy, Bark, webhooks, Home Assistant, Obico ML, the
  slicer sidecars) — loopback and RFC-1918 MUST stay reachable, because
  self-hosting those next to Bambuddy is the normal topology. Blocking them
  would break most installs, which is why a blanket private-IP blocklist is
  the wrong fix here.
- Public-internet (OIDC issuer and icon URLs) — a private address cannot be a
  real IdP, so it is a probe.

Both tiers reject what is dangerous under any topology: non-HTTP schemes,
numeric-encoded IPs, cloud-metadata endpoints, multicast/unspecified, and
IPv4-mapped IPv6 encodings of the above.

The separate concern covered here is *response-body echo*. Notification
provider URLs are writable by anyone holding ``NOTIFICATIONS_CREATE`` — which
the default Operators group carries and which does NOT imply
``SETTINGS_UPDATE`` — and ``POST /notifications/test-config`` takes the URL
from the request body without persisting it. Returning the upstream body there
made an intended reachability check into an authenticated read primitive
against anything the process can reach. Providers whose host Bambuddy pins
(Pushover, Telegram, CallMeBot, Discord) may still echo, since the caller
cannot influence the destination.
"""

from __future__ import annotations

import inspect
import re

import httpx
import pytest

from backend.app.api.routes._oidc_helpers import assert_safe_public_https_url
from backend.app.api.routes._spoolman_helpers import assert_safe_spoolman_url
from backend.app.api.routes._url_safety import assert_safe_lan_service_url
from backend.app.schemas.auth import OIDCProviderCreate, OIDCProviderUpdate
from backend.app.schemas.settings import LAN_SERVICE_URL_SETTINGS, AppSettingsUpdate
from backend.app.services import notification_service as ns
from backend.app.services.homeassistant import HomeAssistantService
from backend.app.services.rest_smart_plug import RESTSmartPlugService
from backend.app.services.tasmota import TasmotaService

# Dangerous under any topology — both tiers must reject all of these.
UNIVERSALLY_BLOCKED = [
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/_INFO",
    "ftp://internal.example.com/",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.100.100.200/",
    "http://[fd00:ec2::254]/",
    "http://2130706433/",
    "http://0x7f000001/",
    "http://[::ffff:169.254.169.254]/",
    "http://0.0.0.0/",
    "http://239.255.255.250/",
    # The DNS-name form of the same target. Neither tier resolves hostnames,
    # but these are a fixed literal set, so matching them costs no lookup.
    "http://metadata.google.internal/",
    "http://METADATA.GOOGLE.INTERNAL/computeMetadata/v1/",
    "http://metadata.goog/",
]

# The normal self-hosted topology — the LAN tier must permit all of these.
LAN_ALLOWED = [
    "http://127.0.0.1:7912/",
    "http://localhost:3003",
    "http://192.168.1.50:8123",
    "http://10.0.0.7:3333",
    "http://172.16.4.9:8080",
    "https://ntfy.example.com/",
    "http://spoolman.lan:7912",
]


# ---------------------------------------------------------------------------
# The LAN-service tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", UNIVERSALLY_BLOCKED)
def test_lan_tier_rejects_universally_dangerous_targets(url: str):
    with pytest.raises(ValueError):
        assert_safe_lan_service_url(url, label="Test URL")


@pytest.mark.parametrize("url", LAN_ALLOWED)
def test_lan_tier_permits_the_normal_self_hosted_topology(url: str):
    """A blanket private-IP block here would break most real installs."""
    assert_safe_lan_service_url(url, label="Test URL")


def test_lan_tier_names_the_field_in_its_error():
    with pytest.raises(ValueError, match="ntfy server URL"):
        assert_safe_lan_service_url("file:///etc/passwd", label="ntfy server URL")


def test_spoolman_wrapper_keeps_its_user_facing_wording():
    """The wording is asserted by pre-existing tests; delegation must not change it."""
    with pytest.raises(ValueError, match="^Spoolman URL must use http or https$"):
        assert_safe_spoolman_url("file:///etc/passwd")
    with pytest.raises(ValueError, match="^Spoolman URL must not point to a cloud metadata endpoint$"):
        assert_safe_spoolman_url("http://169.254.169.254/")


# ---------------------------------------------------------------------------
# The public-internet tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", UNIVERSALLY_BLOCKED)
def test_public_tier_rejects_universally_dangerous_targets(url: str):
    with pytest.raises(ValueError):
        assert_safe_public_https_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://192.168.1.5/",
        "https://10.1.2.3/",
        "https://[fe80::1]/",
        "https://[::ffff:127.0.0.1]/",
        "http://accounts.google.com/",  # scheme must be https
    ],
)
def test_public_tier_additionally_rejects_private_and_plain_http(url: str):
    with pytest.raises(ValueError):
        assert_safe_public_https_url(url)


# ---------------------------------------------------------------------------
# OIDC issuer_url — the encoding bypasses the hand-rolled validator missed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/",  # decimal-encoded 127.0.0.1
        "https://0x7f000001/",  # hex-encoded 127.0.0.1
        "https://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
        "https://[::ffff:169.254.169.254]/",  # IPv4-mapped IMDS
        "https://0.0.0.0/",
        "https://239.255.255.250/",
        "https://169.254.169.254/",
        "https://127.0.0.1/",
        "https://192.168.1.5/",
        "http://idp.example.com/",
    ],
)
def test_issuer_url_rejects_encoded_and_private_targets(url: str):
    with pytest.raises(ValueError):
        OIDCProviderCreate(
            name="SSO",
            issuer_url=url,
            client_id="cid",
            client_secret="secret",
        )


def test_issuer_url_update_is_guarded_too():
    """The update path matters most: it can change the issuer while the stored
    client_secret stays, which is the shape that would exfiltrate a real secret."""
    with pytest.raises(ValueError):
        OIDCProviderUpdate(issuer_url="https://[::ffff:127.0.0.1]/")


def test_issuer_url_error_names_the_field_not_the_icon():
    with pytest.raises(ValueError, match="issuer_url"):
        OIDCProviderUpdate(issuer_url="https://127.0.0.1/")


def test_a_real_idp_still_validates():
    provider = OIDCProviderCreate(
        name="SSO",
        issuer_url="https://accounts.google.com",
        client_id="cid",
        client_secret="secret",
    )
    assert provider.issuer_url == "https://accounts.google.com"


# ---------------------------------------------------------------------------
# Settings URLs
# ---------------------------------------------------------------------------

# Imported from the schema rather than duplicated, so the backstop below cannot
# silently disagree with what is actually validated.
LAN_SERVICE_SETTINGS = LAN_SERVICE_URL_SETTINGS


@pytest.mark.parametrize("field", LAN_SERVICE_SETTINGS)
@pytest.mark.parametrize("url", UNIVERSALLY_BLOCKED)
def test_settings_urls_reject_dangerous_targets(field: str, url: str):
    with pytest.raises(ValueError):
        AppSettingsUpdate(**{field: url})


@pytest.mark.parametrize("field", LAN_SERVICE_SETTINGS)
@pytest.mark.parametrize("url", LAN_ALLOWED)
def test_settings_urls_permit_lan_hosts(field: str, url: str):
    assert AppSettingsUpdate(**{field: url})


@pytest.mark.parametrize("field", LAN_SERVICE_SETTINGS)
@pytest.mark.parametrize("empty", ["", "   "])
def test_settings_urls_accept_empty_meaning_not_configured(field: str, empty: str):
    """Empty is the documented "fall back to the env var" value for all four."""
    assert AppSettingsUpdate(**{field: empty})


@pytest.mark.parametrize("field", LAN_SERVICE_SETTINGS)
@pytest.mark.parametrize(
    "legacy",
    [
        "192.168.1.10:3333",  # urlparse: scheme='', netloc='', hostname=None
        "localhost:3003",  # urlparse: scheme='localhost' (!), hostname=None
        "obico.local:3333",  # same trap, with dots
        "192.168.1.10",
    ],
)
def test_settings_urls_do_not_newly_reject_scheme_less_legacy_values(field: str, legacy: str):
    """Compatibility guard, not an endorsement.

    The settings inputs are plain text with no scheme enforcement, so values
    like these are already in the wild. They are inert — httpx raises
    UnsupportedProtocol, so no request is issued — and they were storable
    before the validator existed. Rejecting them now would block saves of
    unrelated fields bundled in the same request (the Obico panel auto-saves
    obico_ml_url alongside every other Obico setting).
    """
    assert AppSettingsUpdate(**{field: legacy})


@pytest.mark.parametrize("field", LAN_SERVICE_SETTINGS)
def test_settings_urls_still_reject_a_real_non_http_scheme(field: str):
    """The leniency above is scoped to strings that are not URLs at all."""
    with pytest.raises(ValueError):
        AppSettingsUpdate(**{field: "file:///etc/passwd"})


def test_every_url_setting_is_either_guarded_or_explicitly_exempt():
    """CI backstop: a new outbound-URL setting can't land unvalidated.

    Any new ``*_url`` field on AppSettingsUpdate must be added to the
    validator's field tuple or listed as exempt here with a reason. This
    catches the failure mode the original report correctly identified — guards
    added per-incident rather than to the whole class of fields.
    """
    exempt = {
        # Bambuddy's own public address, not a destination it requests. It is
        # rendered into notification bodies and OIDC redirect URIs, and handed
        # to Obico's ML server as the `img` parameter for that server to fetch
        # (obico_detection.py builds `{external_url}/api/v1/obico/cached-frame/
        # {nonce}`). Pointing it at a private address only breaks Bambuddy's own
        # links; it cannot make Bambuddy request anything it otherwise wouldn't.
        "external_url",
        # Guarded by assert_safe_spoolman_url at each consumer (spoolman.py,
        # location_service.py, inventory.py, spoolbuddy.py,
        # spoolman_inventory.py) rather than in the schema, keeping its
        # established user-facing "Spoolman URL ..." error wording.
        "spoolman_url",
        # Not an HTTP URL: ldap:// or ldaps://, handed to an LDAP client, never
        # to httpx. The LAN-service guard requires http/https and would reject
        # every valid value. It also cannot reach a cloud-metadata endpoint,
        # since IMDS only speaks HTTP.
        "ldap_server_url",
    }
    url_fields = {name for name in AppSettingsUpdate.model_fields if name.endswith("_url")}
    unguarded = url_fields - set(LAN_SERVICE_SETTINGS) - exempt
    assert not unguarded, (
        f"New outbound URL setting(s) {sorted(unguarded)} are not covered by a "
        f"SSRF guard. Add them to AppSettingsUpdate._LAN_SERVICE_URL_FIELDS (or "
        f"the public-internet guard), or add them to `exempt` above with a reason."
    )


# ---------------------------------------------------------------------------
# Notification providers: URL guard + no response-body echo
# ---------------------------------------------------------------------------


def _response(status: int = 500, body: str = "root:x:0:0:root:/root:/bin/bash") -> httpx.Response:
    return httpx.Response(status_code=status, text=body, request=httpx.Request("POST", "http://10.0.0.1/"))


SECRET_BODY = "root:x:0:0:root:/root:/bin/bash"


def test_opaque_failure_does_not_return_the_response_body():
    message = ns._opaque_http_failure(_response(), label="webhook endpoint")

    assert SECRET_BODY not in message
    assert "500" in message, "the status code is still useful and is not sensitive"
    assert "webhook endpoint" in message


def test_opaque_failure_logs_the_body_for_the_operator(caplog):
    """The body stays available to whoever administers the host — via logs,
    not via the API response."""
    with caplog.at_level("DEBUG", logger=ns.__name__):
        ns._opaque_http_failure(_response(), label="ntfy server")

    assert SECRET_BODY in caplog.text


@pytest.mark.parametrize(
    "provider_label",
    ["ntfy server", "Bark server", "webhook endpoint", "Home Assistant endpoint"],
)
def test_user_supplied_host_providers_use_the_opaque_path(provider_label: str):
    """Guards the mapping itself: each user-supplied-host provider must route
    its HTTP failure through _opaque_http_failure rather than formatting the
    body inline."""
    src = inspect.getsource(ns)
    assert f'_opaque_http_failure(response, label="{provider_label}")' in src


def test_no_user_supplied_host_provider_formats_the_body_inline():
    """Any remaining ``response.text[:200]`` must belong to a host-pinned provider.

    Pushover/Telegram/CallMeBot/Discord all target hardcoded hosts (Discord via
    a webhook-prefix allowlist), so there is no trust boundary to cross.
    """
    src = inspect.getsource(ns).split("\n")
    host_pinned = {"_send_callmebot", "_send_pushover", "_send_telegram", "_send_discord"}

    current = None
    offenders = []
    for line in src:
        match = re.match(r"\s+async def (_send_\w+)", line)
        if match:
            current = match.group(1)
        if "response.text[:200]" in line and current not in host_pinned:
            offenders.append(current)

    assert not offenders, (
        f"{offenders} echo the upstream response body but do not target a "
        f"hardcoded host. Route the failure through _opaque_http_failure."
    )


@pytest.mark.parametrize("url", UNIVERSALLY_BLOCKED)
def test_provider_url_guard_rejects_dangerous_targets(url: str):
    assert ns._assert_safe_provider_url(url, label="Webhook URL") is not None


@pytest.mark.parametrize("url", LAN_ALLOWED)
def test_provider_url_guard_permits_self_hosted_servers(url: str):
    assert ns._assert_safe_provider_url(url, label="ntfy server URL") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_type", "config"),
    [
        ("ntfy", {"server": "http://169.254.169.254", "topic": "t"}),
        ("bark", {"server": "http://169.254.169.254", "device_key": "k"}),
        ("webhook", {"webhook_url": "http://169.254.169.254/latest/meta-data/"}),
    ],
)
async def test_test_config_refuses_metadata_targets_without_a_request(provider_type: str, config: dict, monkeypatch):
    """The end-to-end shape of the reported attack: an unsaved config aimed at
    IMDS via the test endpoint. It must be refused before any HTTP call."""
    called = False

    async def _fail_if_called(*_a, **_kw):
        nonlocal called
        called = True
        raise AssertionError("outbound request should not have been attempted")

    service = ns.NotificationService()
    monkeypatch.setattr(service, "_get_client", _fail_if_called)

    success, message = await service.send_test_notification(provider_type, config)

    assert success is False
    assert called is False
    assert "cloud metadata" in message


# ---------------------------------------------------------------------------
# Smart plugs: the same request-body-URL shape as the notification test endpoint
# ---------------------------------------------------------------------------
#
# POST /smart-plugs/{ha,rest}/test-connection take their URL from the request
# body and never persist it, so the schema-layer validator on ``ha_url`` does
# not apply. Both are reachable with only ``SMART_PLUGS_CONTROL``, which the
# default Operators group carries and which does NOT imply ``SETTINGS_UPDATE``
# — identical to the notification case above.
#
# Both previously used hand-rolled checks that got the policy wrong in both
# directions: the REST one rejected a literal ``127.0.0.1`` while allowing
# every non-literal hostname, and the HA one matched three literal strings and
# never parsed the hostname as an IP at all.


@pytest.mark.parametrize("url", UNIVERSALLY_BLOCKED)
def test_rest_plug_guard_rejects_dangerous_targets(url: str):
    assert RESTSmartPlugService._validate_url(url) is False


@pytest.mark.parametrize("url", LAN_ALLOWED)
def test_rest_plug_guard_permits_the_normal_self_hosted_topology(url: str):
    """Includes literal 127.0.0.1, which the previous implementation rejected
    while accepting the equivalent "localhost" — a plug bridge on the same
    host could only be configured by spelling it one particular way."""
    assert RESTSmartPlugService._validate_url(url) is True


@pytest.mark.parametrize("url", UNIVERSALLY_BLOCKED)
def test_ha_guard_rejects_dangerous_targets(url: str):
    assert HomeAssistantService._validate_url(url) is None


@pytest.mark.parametrize("url", LAN_ALLOWED)
def test_ha_guard_permits_the_normal_self_hosted_topology(url: str):
    assert HomeAssistantService._validate_url(url) is not None


def test_ha_guard_still_normalises_the_url_it_returns():
    """Delegating the policy must not change what the caller gets back:
    scheme+host+port+path, with query and fragment dropped."""
    assert HomeAssistantService._validate_url("http://192.168.1.5:8123/base?x=1#f") == "http://192.168.1.5:8123/base"
    assert HomeAssistantService._validate_url("http://ha.lan") == "http://ha.lan"


def test_ha_guard_keeps_ipv6_literals_bracketed():
    """urlparse strips the brackets off an IPv6 host; re-emitting it without
    them yields an unparseable URL that httpx cannot dial."""
    assert HomeAssistantService._validate_url("http://[fd00::1]:8123/api") == "http://[fd00::1]:8123/api"


@pytest.mark.parametrize(
    "ip",
    [
        "169.254.169.254",
        "100.100.100.200",
        "fd00:ec2::254",
        "0.0.0.0",  # nosec B104 — rejection fixture, not a bind address: the assertion below is that the guard refuses it
        "239.255.255.250",
    ],
)
def test_tasmota_guard_rejects_metadata_and_misuse_addresses(ip: str):
    """Tasmota keeps its own stricter rule (bare IP literals only, loopback
    rejected — a plug is always a separate LAN device), but must not miss the
    destinations that are dangerous regardless of topology."""
    assert TasmotaService._validate_ip(ip) is False


@pytest.mark.parametrize("ip", ["::ffff:169.254.169.254", "::ffff:100.100.100.200"])
def test_tasmota_guard_unwraps_ipv4_mapped_ipv6(ip: str):
    assert TasmotaService._validate_ip(ip) is False


@pytest.mark.parametrize("ip", ["192.168.1.50", "10.0.0.7", "172.16.4.9"])
def test_tasmota_guard_still_permits_a_normal_lan_plug(ip: str):
    assert TasmotaService._validate_ip(ip) is True


@pytest.mark.parametrize("ip", ["127.0.0.1", "tasmota.local", "not-an-ip"])
def test_tasmota_guard_keeps_failing_closed_on_non_lan_device_values(ip: str):
    """Deliberately stricter than the shared LAN guard, and unchanged here."""
    assert TasmotaService._validate_ip(ip) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["http://169.254.169.254/", "http://100.100.100.200/", "http://metadata.google.internal/"],
)
async def test_rest_test_connection_refuses_metadata_without_a_request(target: str, monkeypatch):
    """End-to-end shape of the reported attack, mirroring the notification
    test above: an unsaved URL aimed at IMDS via the test endpoint must be
    refused before any HTTP call is made."""

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("outbound request should not have been attempted")

    monkeypatch.setattr(httpx, "AsyncClient", _fail_if_called)
    result = await RESTSmartPlugService().test_connection(target, "GET", None)

    assert result["success"] is False
    assert "cloud metadata" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["http://169.254.169.254", "http://100.100.100.200", "http://metadata.google.internal"],
)
async def test_ha_test_connection_refuses_metadata_without_a_request(target: str, monkeypatch):
    def _fail_if_called(*_a, **_kw):
        raise AssertionError("outbound request should not have been attempted")

    monkeypatch.setattr(httpx, "AsyncClient", _fail_if_called)
    result = await HomeAssistantService().test_connection(target, "token")

    assert result["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["http://169.254.169.254", "http://metadata.google.internal"])
async def test_obico_test_connection_refuses_metadata_without_a_request(target: str, monkeypatch):
    """Same shape again: obico_ml_url is guarded when saved via settings, but
    this route takes the URL from the request body and echoes the response."""
    from backend.app.services.obico_detection import ObicoDetectionService

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("outbound request should not have been attempted")

    monkeypatch.setattr(httpx, "AsyncClient", _fail_if_called)
    result = await ObicoDetectionService().test_connection(target)

    assert result["ok"] is False
    assert result["body"] is None
    assert "cloud metadata" in result["error"]


# ---------------------------------------------------------------------------
# Drift backstop, part 2: URLs that arrive in a request body
# ---------------------------------------------------------------------------
#
# `test_every_url_setting_is_either_guarded_or_explicitly_exempt` above only
# walks `AppSettingsUpdate`. That is why the notification test endpoint, and
# then the two smart-plug test endpoints, each had to be found by hand: a URL
# that arrives in a request body and is never persisted is not a settings
# field, so nothing enumerated it. This walks the live route table instead.


def _request_body_url_fields() -> set[tuple[str, str]]:
    """Every (model, field) pair on a mutating route whose body carries a URL."""
    from fastapi.routing import APIRoute
    from pydantic import BaseModel

    from backend.app.main import app

    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not ({"POST", "PUT", "PATCH"} & set(route.methods or ())):
            continue
        for param in route.dependant.body_params:
            # FastAPI moved the resolved annotation from `type_` onto
            # `field_info.annotation`; read both so this can't silently
            # enumerate nothing (which would make the assertions vacuous).
            annotation = getattr(param, "type_", None)
            if annotation is None:
                annotation = getattr(getattr(param, "field_info", None), "annotation", None)
            if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
                continue
            for field in annotation.model_fields:
                if field == "url" or field.endswith("_url"):
                    found.add((annotation.__name__, field))
    return found


# Guarded: the handler (or the service it calls) puts the value through one of
# the two tiers before any request is issued.
GUARDED_BODY_URLS = {
    ("AppSettingsUpdate", "bambu_studio_api_url"),
    ("AppSettingsUpdate", "ha_url"),
    ("AppSettingsUpdate", "obico_ml_url"),
    ("AppSettingsUpdate", "orcaslicer_api_url"),
    ("AppSettingsUpdate", "spoolman_url"),  # assert_safe_spoolman_url at each consumer
    ("HATestConnectionRequest", "url"),  # homeassistant._validate_url
    ("RESTTestConnectionRequest", "url"),  # rest_smart_plug._validate_url
    ("TestConnectionRequest", "url"),  # obico_detection.test_connection
    ("OIDCProviderCreate", "issuer_url"),  # public tier, via schemas.auth
    ("OIDCProviderCreate", "icon_url"),
    ("OIDCProviderUpdate", "issuer_url"),
    ("OIDCProviderUpdate", "icon_url"),
    # Gitea/Forgejo derive their API base from this and request it with the
    # stored token, so it is a real fetch target — guarded in
    # github_backup._enforce_private_repo, which both POST and PATCH funnel through.
    ("GitHubBackupConfigCreate", "repository_url"),
    ("GitHubBackupConfigUpdate", "repository_url"),
    # SmartPlug{Create,Update} persist these; every read goes back out through
    # RESTSmartPlugService._send_request, which applies the same guard.
    ("SmartPlugCreate", "rest_on_url"),
    ("SmartPlugCreate", "rest_off_url"),
    ("SmartPlugCreate", "rest_status_url"),
    ("SmartPlugCreate", "rest_power_url"),
    ("SmartPlugCreate", "rest_energy_url"),
    ("SmartPlugUpdate", "rest_on_url"),
    ("SmartPlugUpdate", "rest_off_url"),
    ("SmartPlugUpdate", "rest_status_url"),
    ("SmartPlugUpdate", "rest_power_url"),
    ("SmartPlugUpdate", "rest_energy_url"),
}

# Not a destination Bambuddy requests — no guard applies.
NOT_A_FETCH_TARGET = {
    ("AppSettingsUpdate", "external_url"),  # Bambuddy's own address (see exempt list above)
    ("AppSettingsUpdate", "ldap_server_url"),  # ldap://, handed to an LDAP client
    ("ProjectCreate", "url"),  # stored link, rendered in the UI, never fetched
    ("ProjectUpdate", "url"),
    ("BOMItemCreate", "sourcing_url"),  # stored supplier link, never fetched
    ("BOMItemUpdate", "sourcing_url"),
    ("MakerWorldResolveRequest", "url"),  # parsed for a model id; fetches go to a pinned CDN allowlist
    ("DeviceRegisterRequest", "backend_url"),  # the device's view of Bambuddy's own address
    ("HeartbeatRequest", "backend_url"),
    ("SystemConfigRequest", "backend_url"),
    ("ExternalLinkCreate", "url"),  # sidebar link, rendered in the UI, never requested
    ("ExternalLinkUpdate", "url"),
    ("MaintenanceTypeCreate", "wiki_url"),  # documentation link surfaced in the UI/notifications
    ("MaintenanceTypeUpdate", "wiki_url"),
    ("ArchiveUpdate", "external_url"),  # stored source link for the model, never fetched
}

# Genuinely unguarded, and deliberately recorded rather than quietly exempted.
# These reach `external_camera.capture_frame`, which dials rtsp:// as well as
# http(s):// — the LAN-service guard rejects any non-HTTP scheme, so wiring it
# up as-is would break every RTSP camera. Closing these needs a scheme-aware
# variant of the guard, not a one-line delegation.
KNOWN_UNGUARDED_NEEDS_SCHEME_AWARE_GUARD = {
    ("PrinterCreate", "external_camera_url"),
    ("PrinterCreate", "external_camera_snapshot_url"),
    ("PrinterUpdate", "external_camera_url"),
    ("PrinterUpdate", "external_camera_snapshot_url"),
}


def test_the_route_walk_actually_finds_something():
    """Guards the guard. If FastAPI's internals move again and the walk starts
    returning nothing, both assertions below pass vacuously and the backstop
    silently stops working — which is the exact failure it exists to prevent."""
    found = _request_body_url_fields()
    assert ("RESTTestConnectionRequest", "url") in found
    assert ("HATestConnectionRequest", "url") in found
    assert len(found) > 20


def test_every_request_body_url_is_classified():
    """A new URL-bearing request field can't land without a decision.

    Add it to GUARDED_BODY_URLS once the handler runs it through a guard, or
    to NOT_A_FETCH_TARGET with the reason it is never requested. Do not add
    anything to KNOWN_UNGUARDED_* without also raising it.
    """
    classified = GUARDED_BODY_URLS | NOT_A_FETCH_TARGET | KNOWN_UNGUARDED_NEEDS_SCHEME_AWARE_GUARD
    unclassified = _request_body_url_fields() - classified
    assert not unclassified, (
        f"Unclassified request-body URL field(s): {sorted(unclassified)}. Route the value "
        f"through a guard and list it in GUARDED_BODY_URLS, or list it in NOT_A_FETCH_TARGET "
        f"with the reason it is never fetched."
    )


def test_classification_lists_do_not_drift_from_the_routes():
    """The reverse direction: a stale entry means a route was renamed or
    removed and the list was not updated, which would hide the next one."""
    actual = _request_body_url_fields()
    stale = (GUARDED_BODY_URLS | NOT_A_FETCH_TARGET | KNOWN_UNGUARDED_NEEDS_SCHEME_AWARE_GUARD) - actual
    assert not stale, f"Classification entries no longer match any route: {sorted(stale)}"
