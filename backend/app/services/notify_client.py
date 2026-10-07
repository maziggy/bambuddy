"""Notify! Partner API client. Resource retries belong to the lifecycle owner.

The gateway requires a query token, so never expose request URLs or response
bodies in errors. In particular an uncertain start must retain its activity ID
instead of being retried as a new tile. Contract: getnotifyapp.com/apidocs/.
"""

import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from backend.app.core.logging_filters import redact_query_tokens

BASE_URL = "https://push.getnotifyapp.com"
_ID = re.compile(r"[A-Za-z0-9]{8,32}\Z")
_ACTIVITY_ID = re.compile(r"LA[A-Z0-9]{6}\Z")
_WIDGET_ID = re.compile(r"WG[A-Z0-9]{6}\Z")


class _NotifyTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx formats URL *objects*, which the string-only access-log
        # filter cannot redact. Cover debug sessions as well as normal use.
        message = record.getMessage()
        if BASE_URL in message:
            record.msg = redact_query_tokens(message)
            record.args = ()
        return True


_http_logger = logging.getLogger("httpx")
if not any(isinstance(item, _NotifyTokenFilter) for item in _http_logger.filters):
    _http_logger.addFilter(_NotifyTokenFilter())


class NotifyError(Exception):
    """Safe user-facing error plus the gateway's structured recovery hints."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: dict | None = None,
        retry_after_seconds: int | None = None,
        delivery_state: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}
        activity_id = self.payload.get("activityId")
        self.activity_id = activity_id if isinstance(activity_id, str) and _ACTIVITY_ID.fullmatch(activity_id) else None
        widget_id = self.payload.get("widgetId")
        self.widget_id = widget_id if isinstance(widget_id, str) and _WIDGET_ID.fullmatch(widget_id) else None
        self.delivery_state = delivery_state or self.payload.get("deliveryState")
        self.retry_after_seconds = retry_after_seconds
        self.opening_the_app_may_help = self.payload.get("openingTheAppMayHelp") is True


def notify_credentials(config: dict) -> tuple[str, str]:
    """Accept legacy and newer device IDs, and group IDs, without guessing a platform."""
    device_id = config.get("device_id")
    token = config.get("token")
    if not isinstance(device_id, str) or not _ID.fullmatch(device_id.strip()):
        raise NotifyError("Notify! requires a valid device or group ID (8–32 letters and digits)")
    if not isinstance(token, str) or not token.strip():
        raise NotifyError("Notify! requires a device or group token")
    for field in (
        "live_activities",
        "lock_screen_widgets",
        "live_activity_privacy",
        "live_activity_stage",
        "time_sensitive",
    ):
        if field in config and not isinstance(config[field], bool):
            raise NotifyError(f"Notify! {field} must be a boolean")
    if config.get("live_activities") is True and device_id.strip().upper().startswith(("GRP", "WB", "MC")):
        raise NotifyError("Notify! Live Activities require an iOS device ID")
    if config.get("lock_screen_widgets") is True and device_id.strip().upper().startswith(("GRP", "WB", "MC")):
        raise NotifyError("Notify! Lock Screen widgets require an iOS device ID")
    for field in ("icon_url", "live_activity_button_url"):
        if config.get(field) and not _https_url(config[field]):
            raise NotifyError(f"Notify! {field} must be an HTTPS URL without embedded credentials")
    button = config.get("live_activity_button_url")
    if button and len(button) > 512:
        raise NotifyError("Notify! dashboard URL must be at most 512 characters")
    if config.get("live_activity_style") not in (None, "", "bar", "segments", "none"):
        raise NotifyError("Notify! progress style must be bar, segments, or none")
    tint = config.get("live_activity_tint")
    if tint and (not isinstance(tint, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", tint)):
        raise NotifyError("Notify! tint must be a color in #RRGGBB format")
    symbol = config.get("live_activity_symbol")
    if symbol and (not isinstance(symbol, str) or len(symbol) > 64 or "\x00" in symbol):
        raise NotifyError("Notify! symbol must be at most 64 characters without NUL")
    metrics = config.get("live_activity_metrics", [])
    choices = {"progress", "eta", "layers", "nozzle", "bed", "chamber"}
    if (
        not isinstance(metrics, list)
        or len(metrics) > 6
        or any(not isinstance(m, str) or m not in choices for m in metrics)
    ):
        raise NotifyError("Notify! metrics must contain up to six supported metric names")
    return device_id.strip(), token.strip()


def _https_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return value.strip()
    except ValueError:
        pass
    return None


def notify_supports_photos(device_id: Any) -> bool:
    """Browser and group targets receive text notifications without camera photos."""
    return isinstance(device_id, str) and not device_id.strip().upper().startswith(("WB", "GRP"))


class NotifyClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _request(
        self, method: str, path: str, token: str, *, params: dict | None = None, content: dict | None = None
    ) -> dict:
        if not isinstance(token, str) or not token.strip():
            raise NotifyError("Notify! requires a device or group token")
        try:
            response = await self.client.request(
                method,
                f"{BASE_URL}{path}",
                params={"token": token.strip(), **(params or {})},
                json=content,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            # A timeout after a start may mean Apple accepted it. Do not put
            # str(exc) into a notification log: it can include the query token.
            raise NotifyError("Notify! could not be reached", delivery_state="unknown") from None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if not 200 <= response.status_code < 300 or payload.get("success") is False:
            retry = payload.get("retryAfterSeconds", response.headers.get("Retry-After"))
            try:
                retry = max(1, int(retry)) if retry is not None else None
            except (ValueError, TypeError, OverflowError):
                retry = None
            messages = {
                400: "Notify! rejected the request; check the device registration and message",
                401: "Notify! rejected the credentials",
                403: "Notify! rejected the credentials or resource ID",
                404: "Notify! device or resource was not found",
                409: "Notify! could not apply the request to the current resource",
                410: "Notify! resource is no longer available",
                429: "Notify! rate limit reached; wait before retrying",
                502: "Notify! could not confirm delivery",
                503: "Notify! is temporarily unavailable",
            }
            if path.startswith("/live-activity/"):
                messages.update(
                    {
                        400: "Notify! rejected the request; check device support and the five Live Activity limit",
                        409: "Open Notify! on the iOS device and enable Live Activities before starting a tile",
                        410: "Notify! Live Activity has ended",
                    }
                )
            elif path.startswith("/widgets/"):
                messages.update(
                    {
                        400: "Notify! rejected the widget request; check its content and the ten widget limit",
                        403: "Notify! rejected the credentials or widget ID",
                        409: "Notify! requires a precise widget ID when the device has multiple widgets",
                    }
                )
            raise NotifyError(
                messages.get(response.status_code, f"Notify! request failed (HTTP {response.status_code})"),
                status_code=response.status_code,
                payload=payload,
                retry_after_seconds=retry,
            )
        if not payload:
            raise NotifyError("Notify! returned an invalid response", delivery_state="unknown")
        return payload

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value.strip()):
            raise NotifyError("Notify! ID must contain 8–32 letters and digits")
        return value.strip()

    @staticmethod
    def _activity_id(value: str) -> str:
        if not isinstance(value, str) or not _ACTIVITY_ID.fullmatch(value):
            raise NotifyError("Notify! updates and ends require a precise activity ID")
        return value

    @staticmethod
    def _widget_id(value: str) -> str:
        if not isinstance(value, str) or not _WIDGET_ID.fullmatch(value):
            raise NotifyError("Notify! widget reads, updates and deletes require a precise widget ID")
        return value

    async def validate(self, device_id: str, token: str) -> dict:
        return await self._request("GET", "/link", token, params={"id": self._id(device_id)})

    async def send_notification(
        self,
        device_id: str,
        token: str,
        *,
        title: str,
        text: str,
        group_type: str | None = None,
        icon_url: str | None = None,
        image_url: str | None = None,
        time_sensitive: bool = False,
    ) -> dict:
        content: dict[str, Any] = {"title": title, "text": text}
        if group_type:
            content["groupType"] = group_type
        if time_sensitive:
            content["timeSensitive"] = True
        if icon_url:
            icon = _https_url(icon_url)
            if not icon:
                raise NotifyError("Notify! icon URL must use HTTPS")
            content["iconUrl"] = icon
        # Photos use Bambuddy's existing public photo URL. A LAN/HTTP-only
        # installation still gets the text, without a broken remote image.
        if (image := _https_url(image_url)) and notify_supports_photos(device_id):
            content["imageUrl"] = image
        result = await self._request("POST", f"/notify-json/{self._id(device_id)}", token, content=content)
        if result.get("success") is not True:
            raise NotifyError("Notify! did not confirm notification delivery")
        if result.get("failureCount", 0):
            raise NotifyError("Notify! could not deliver to every device in the group")
        return result

    async def start_activity(self, device_id: str, token: str, content: dict) -> dict:
        device_id = self._id(device_id)
        if _ACTIVITY_ID.fullmatch(device_id):
            # Legacy device IDs can begin with LA. The gateway resolves IDs by
            # lookup, so new=1 against an activity ID would update someone
            # else's existing tile rather than create ours. Prove this is a
            # registered device without rejecting legitimate legacy IDs.
            identity = await self.validate(device_id, token)
            if identity.get("type") != "device":
                raise NotifyError("Notify! Live Activities require a device ID, not an activity or group ID")
        result = await self._request("POST", f"/live-activity/{device_id}", token, params={"new": "1"}, content=content)
        activity_id = result.get("activityId")
        if not isinstance(activity_id, str) or not _ACTIVITY_ID.fullmatch(activity_id):
            raise NotifyError("Notify! did not return an activity ID", delivery_state="unknown")
        return result

    async def update_activity(self, activity_id: str, token: str, content: dict) -> dict:
        return await self._request("POST", f"/live-activity/{self._activity_id(activity_id)}", token, content=content)

    async def end_activity(self, activity_id: str, token: str, content: dict | None = None) -> dict:
        return await self._request("DELETE", f"/live-activity/{self._activity_id(activity_id)}", token, content=content)

    async def get_activity(self, activity_id: str, token: str) -> dict:
        return await self._request("GET", f"/live-activity/{self._id(activity_id)}", token)

    async def create_widget(self, device_id: str, token: str, content: dict) -> dict:
        try:
            result = await self._request(
                "POST", f"/widgets/{self._id(device_id)}", token, params={"new": "1"}, content=content
            )
        except NotifyError as error:
            # A server error may follow a committed create. A 503 explicitly
            # refuses writes; other uncertain creates must not be retried blind.
            if error.status_code and error.status_code >= 500 and error.status_code != 503 and not error.delivery_state:
                error.delivery_state = "unknown"
            raise
        widget_id = result.get("widgetId")
        if not isinstance(widget_id, str) or not _WIDGET_ID.fullmatch(widget_id):
            raise NotifyError("Notify! did not return a widget ID", delivery_state="unknown")
        return result

    async def update_widget(self, widget_id: str, token: str, content: dict) -> dict:
        return await self._request("POST", f"/widgets/{self._widget_id(widget_id)}", token, content=content)

    async def get_widget(self, widget_id: str, token: str) -> dict:
        return await self._request("GET", f"/widgets/{self._widget_id(widget_id)}", token)

    async def list_widgets(self, device_id: str, token: str) -> dict:
        result = await self._request("GET", f"/widgets/{self._id(device_id)}", token)
        widgets = result.get("widgets")
        if not isinstance(widgets, list) or any(
            not isinstance(widget, dict)
            or not isinstance(widget.get("widgetId"), str)
            or not _WIDGET_ID.fullmatch(widget["widgetId"])
            for widget in widgets
        ):
            # Cleanup uses this list to distinguish invalid credentials from
            # an already deleted widget. A malformed reply cannot prove absence.
            raise NotifyError("Notify! returned an invalid widget list", delivery_state="unknown")
        return result

    async def delete_widget(self, widget_id: str, token: str) -> dict:
        return await self._request("DELETE", f"/widgets/{self._widget_id(widget_id)}", token)
