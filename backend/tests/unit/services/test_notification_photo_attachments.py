"""Unit tests for camera snapshot attachments on notifications.

Covers the per-provider attach_photo opt-out, the fetched-URL providers (Home
Assistant, Bark, Slack-format webhooks) and the on-disk store they fetch from,
and the inline image on emails.
"""

import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.schemas.notification_template import EVENT_VARIABLES, PHOTO_CAPABLE_EVENTS
from backend.app.services.email_service import send_email
from backend.app.services.notification_service import NotificationService
from backend.app.utils import notification_photos


def _client_returning(status_code=200, json_body=None):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = ""
    mock_response.json = MagicMock(return_value=json_body or {})
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


def _provider(provider_type: str, config: dict, attach_photo: bool = True):
    provider = MagicMock()
    provider.provider_type = provider_type
    provider.config = json.dumps(config)
    provider.quiet_hours_enabled = False
    provider.attach_photo = attach_photo
    return provider


@pytest.fixture
def service():
    return NotificationService()


class TestNotificationPhotoStore:
    """The flat directory HA/Bark/Slack fetch ad-hoc snapshots from."""

    @pytest.fixture(autouse=True)
    def _base_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(notification_photos.settings, "base_dir", tmp_path)
        self.directory = tmp_path / "notification_photos"

    def test_save_then_find_round_trips(self):
        filename = notification_photos.save_notification_photo(b"jpeg-bytes", "plate_not_empty")

        assert filename.startswith("plate_not_empty_")
        assert filename.endswith(".jpg")
        found = notification_photos.find_notification_photo(filename)
        assert found is not None
        assert found.read_bytes() == b"jpeg-bytes"

    def test_event_type_is_sanitised_into_the_filename(self):
        filename = notification_photos.save_notification_photo(b"x", "../../etc/passwd")

        assert "/" not in filename
        assert ".." not in filename
        assert (self.directory / filename).exists()

    def test_filename_embeds_a_urlsafe_token(self):
        """The filename is the URL's only credential, so it has to carry the
        full 24-byte token, not a guessable suffix."""
        with patch.object(notification_photos.secrets, "token_urlsafe", return_value="T" * 32) as mock_token:
            filename = notification_photos.save_notification_photo(b"x", "test")

        mock_token.assert_called_once_with(24)
        assert filename.endswith("_" + "T" * 32 + ".jpg")

    def test_filenames_are_unique_per_save(self):
        first = notification_photos.save_notification_photo(b"x", "test")
        second = notification_photos.save_notification_photo(b"x", "test")

        assert first != second

    def test_find_rejects_traversal_and_missing_files(self):
        notification_photos.save_notification_photo(b"x", "test")

        assert notification_photos.find_notification_photo("../secret.jpg") is None
        assert notification_photos.find_notification_photo("does_not_exist.jpg") is None

    @pytest.mark.parametrize(
        "name",
        [
            # the pre-#3089-review shape: an 8-hex uuid suffix is guessable
            "test_20260930_120000_abcdef12.jpg",
            # token one character short / long
            "test_20260930_120000_" + "A" * 31 + ".jpg",
            "test_20260930_120000_" + "A" * 33 + ".jpg",
            # other extensions, even if such a file exists on disk
            "test_20260930_120000_" + "A" * 32 + ".png",
            "test_20260930_120000_" + "A" * 32 + ".jpg.bak",
        ],
    )
    def test_find_serves_only_the_exact_saved_shape(self, name):
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / name).write_bytes(b"x")

        assert notification_photos.find_notification_photo(name) is None

    def test_find_refuses_a_photo_past_its_expiry_even_before_prune(self):
        """The 3-day age limit is the URL's expiry, so it can't depend on
        another notification coming along to trigger a prune."""
        filename = notification_photos.save_notification_photo(b"x", "test")
        old = time.time() - notification_photos._MAX_AGE_SECONDS - 60
        os.utime(self.directory / filename, (old, old))

        assert notification_photos.find_notification_photo(filename) is None

    def test_save_prunes_photos_older_than_max_age(self):
        self.directory.mkdir(parents=True)
        stale = self.directory / "stale.jpg"
        stale.write_bytes(b"old")
        old = time.time() - notification_photos._MAX_AGE_SECONDS - 60
        os.utime(stale, (old, old))
        fresh = self.directory / "fresh.jpg"
        fresh.write_bytes(b"new")

        notification_photos.save_notification_photo(b"x", "test")

        assert not stale.exists()
        assert fresh.exists()


class TestPhotoCapableEvents:
    def test_every_photo_capable_event_is_a_known_event(self):
        """supports_photo is looked up per EVENT_VARIABLES key, so a typo here
        would silently never show the preview."""
        assert set(EVENT_VARIABLES) >= PHOTO_CAPABLE_EVENTS


class TestAttachPhotoOptOut:
    """provider.attach_photo=False must keep the photo off every provider type."""

    @pytest.mark.asyncio
    async def test_byte_upload_provider_gets_no_image_when_opted_out(self, service):
        provider = _provider("ntfy", {"topic": "t"}, attach_photo=False)

        with patch.object(service, "_send_ntfy", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = (True, "OK")
            await service._send_to_provider(provider, "Title", "Body", db=AsyncMock(), image_data=b"jpeg")

        assert mock_send.call_args.kwargs.get("image_data") is None

    @pytest.mark.asyncio
    async def test_byte_upload_provider_gets_image_by_default(self, service):
        provider = _provider("ntfy", {"topic": "t"})

        with patch.object(service, "_send_ntfy", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = (True, "OK")
            await service._send_to_provider(provider, "Title", "Body", db=AsyncMock(), image_data=b"jpeg")

        assert mock_send.call_args.kwargs.get("image_data") == b"jpeg"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("provider_type", "config", "sender"),
        [
            ("bark", {"device_key": "abc"}, "_send_bark"),
            ("gotify", {"server": "https://g.example", "app_token": "t"}, "_send_gotify"),
            ("homeassistant", {"service": "notify.mobile_app_x"}, "_send_homeassistant"),
            ("webhook", {"webhook_url": "http://hook.local", "payload_format": "slack"}, "_send_webhook"),
        ],
    )
    async def test_fetched_url_providers_skip_the_url_when_opted_out(self, service, provider_type, config, sender):
        """HA/Bark/Slack could otherwise get the URL an earlier provider in the
        same send left in the shared photo cache, so they need their own check."""
        provider = _provider(provider_type, config, attach_photo=False)
        photo_cache = {"url": "https://bambuddy.example/api/v1/notifications/photos/a.jpg"}

        with (
            patch.object(service, sender, new_callable=AsyncMock) as mock_send,
            patch.object(service, "_get_or_build_photo_url", wraps=service._get_or_build_photo_url) as mock_build,
        ):
            mock_send.return_value = (True, "OK")
            await service._send_to_provider(
                provider, "Title", "Body", db=AsyncMock(), image_data=b"jpeg", photo_cache=photo_cache
            )

        mock_build.assert_not_called()
        assert mock_send.call_args.kwargs.get("image_url") is None

    @pytest.mark.asyncio
    async def test_one_send_persists_the_photo_once_and_keeps_it_out_of_variables(self, service):
        """Every HA/Bark/Slack provider in a send shares one saved photo, and
        the URL (the photo's only credential) never reaches the variables a
        generic webhook copies into its payload."""
        providers = [
            _provider("bark", {"device_key": "abc"}),
            _provider("homeassistant", {"service": "notify.mobile_app_x"}),
        ]
        for i, provider in enumerate(providers):
            provider.id = i
            provider.name = f"p{i}"
            provider.daily_digest_enabled = False
        variables = {"printer": "X1C"}

        with (
            patch.object(service, "_send_bark", new_callable=AsyncMock, return_value=(True, "OK")) as bark,
            patch.object(service, "_send_homeassistant", new_callable=AsyncMock, return_value=(True, "OK")) as ha,
            patch.object(service, "_update_provider_status", new_callable=AsyncMock),
            patch.object(service, "_log_notification", new_callable=AsyncMock),
            patch(
                "backend.app.api.routes.settings.get_setting",
                new_callable=AsyncMock,
                return_value="https://bambuddy.example",
            ),
            patch(
                "backend.app.services.notification_service.save_notification_photo", return_value="p.jpg"
            ) as mock_save,
        ):
            await service._send_to_providers(
                providers, "T", "B", AsyncMock(), event_type="print_complete", image_data=b"jpeg", variables=variables
            )

        mock_save.assert_called_once()
        url = "https://bambuddy.example/api/v1/notifications/photos/p.jpg"
        assert bark.call_args.kwargs["image_url"] == url
        assert ha.call_args.kwargs["image_url"] == url
        assert variables == {"printer": "X1C"}

    @pytest.mark.asyncio
    async def test_bark_gets_photo_url_when_enabled(self, service):
        provider = _provider("bark", {"device_key": "abc"})

        with (
            patch.object(service, "_send_bark", new_callable=AsyncMock) as mock_send,
            patch.object(service, "_get_or_build_photo_url", new_callable=AsyncMock) as mock_build,
        ):
            mock_send.return_value = (True, "OK")
            mock_build.return_value = "https://bambuddy.example/photo.jpg"
            await service._send_to_provider(provider, "Title", "Body", db=AsyncMock(), image_data=b"jpeg")

        assert mock_send.call_args.kwargs.get("image_url") == "https://bambuddy.example/photo.jpg"

    @pytest.mark.asyncio
    async def test_gotify_gets_photo_url_when_enabled(self, service):
        """Gotify takes no uploads, so its photo has to be a URL its app fetches (#2743)."""
        provider = _provider("gotify", {"server": "https://g.example", "app_token": "t"})

        with (
            patch.object(service, "_send_gotify", new_callable=AsyncMock) as mock_send,
            patch.object(service, "_get_or_build_photo_url", new_callable=AsyncMock) as mock_build,
        ):
            mock_send.return_value = (True, "OK")
            mock_build.return_value = "https://bambuddy.example/photo.jpg"
            await service._send_to_provider(provider, "Title", "Body", db=AsyncMock(), image_data=b"jpeg")

        assert mock_send.call_args.kwargs.get("image_url") == "https://bambuddy.example/photo.jpg"

    @pytest.mark.asyncio
    async def test_generic_webhook_never_builds_a_photo_url(self, service):
        """The generic format carries base64 bytes, so it must not pay for a disk write."""
        provider = _provider("webhook", {"webhook_url": "http://hook.local"})

        with (
            patch.object(service, "_send_webhook", new_callable=AsyncMock) as mock_send,
            patch.object(service, "_get_or_build_photo_url", new_callable=AsyncMock) as mock_build,
        ):
            mock_send.return_value = (True, "OK")
            await service._send_to_provider(provider, "Title", "Body", db=AsyncMock(), image_data=b"jpeg")

        mock_build.assert_not_called()
        assert mock_send.call_args.kwargs.get("image_data") == b"jpeg"


class TestFetchedUrlPayloads:
    @pytest.mark.asyncio
    async def test_bark_sends_photo_as_icon(self, service):
        mock_client = _client_returning(200, {"code": 200})

        with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=mock_client):
            await service._send_bark({"device_key": "abc"}, "T", "B", image_url="https://x.example/p.jpg")

        assert mock_client.post.call_args.kwargs["json"]["icon"] == "https://x.example/p.jpg"

    @pytest.mark.asyncio
    async def test_bark_keeps_tap_url_alongside_photo(self, service):
        mock_client = _client_returning(200, {"code": 200})

        with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=mock_client):
            await service._send_bark(
                {"device_key": "abc"}, "T", "B", url="https://x.example/confirm", image_url="https://x.example/p.jpg"
            )

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["url"] == "https://x.example/confirm"
        assert payload["icon"] == "https://x.example/p.jpg"

    @pytest.mark.asyncio
    async def test_slack_webhook_attaches_photo_url(self, service):
        mock_client = _client_returning()
        config = {"webhook_url": "http://mattermost.local/hooks/abc", "payload_format": "slack"}

        with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=mock_client):
            await service._send_webhook(config, "T", "B", image_data=b"jpeg", image_url="https://x.example/p.jpg")

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["attachments"] == [{"fallback": "T", "image_url": "https://x.example/p.jpg"}]
        assert "image" not in payload

    @pytest.fixture
    def ha_settings(self):
        with patch(
            "backend.app.api.routes.settings.get_homeassistant_settings",
            new_callable=AsyncMock,
            return_value={"ha_url": "http://ha.local:8123", "ha_token": "tok", "ha_enabled": True},
        ):
            yield

    @pytest.mark.asyncio
    async def test_homeassistant_notify_service_gets_data_image(self, service, ha_settings):
        mock_client = _client_returning()

        with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=mock_client):
            await service._send_homeassistant(
                {"service": "notify.mobile_app_x"}, "T", "B", db=AsyncMock(), image_url="https://x.example/p.jpg"
            )

        assert mock_client.post.call_args.kwargs["json"]["data"] == {"image": "https://x.example/p.jpg"}

    @pytest.mark.asyncio
    async def test_homeassistant_persistent_notification_never_gets_image(self, service, ha_settings):
        """persistent_notification.create 400s on unknown keys, so the default
        service must not get a data block just because a photo exists."""
        mock_client = _client_returning()

        with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=mock_client):
            await service._send_homeassistant({}, "T", "B", db=AsyncMock(), image_url="https://x.example/p.jpg")

        assert "data" not in mock_client.post.call_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_homeassistant_user_data_image_wins(self, service, ha_settings):
        mock_client = _client_returning()
        config = {"service": "notify.mobile_app_x", "data": json.dumps({"image": "mine", "ttl": 0})}

        with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=mock_client):
            await service._send_homeassistant(config, "T", "B", db=AsyncMock(), image_url="https://x.example/p.jpg")

        assert mock_client.post.call_args.kwargs["json"]["data"] == {"image": "mine", "ttl": 0}


class TestGetOrBuildPhotoUrl:
    @pytest.fixture
    def external_url(self):
        with patch(
            "backend.app.api.routes.settings.get_setting",
            new_callable=AsyncMock,
            return_value="https://bambuddy.example/",
        ):
            yield

    @pytest.mark.asyncio
    async def test_no_image_returns_none(self, service):
        assert await service._get_or_build_photo_url(AsyncMock(), None, "x") is None

    @pytest.mark.asyncio
    async def test_no_external_url_returns_none(self, service):
        """A relative path is useless to a service that fetches it itself."""
        with (
            patch("backend.app.api.routes.settings.get_setting", new_callable=AsyncMock, return_value=""),
            patch("backend.app.services.notification_service.save_notification_photo") as mock_save,
        ):
            result = await service._get_or_build_photo_url(AsyncMock(), b"jpeg", "x")

        assert result is None
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_saves_and_builds_a_url_with_no_token(self, service, external_url):
        """The filename is the credential. A camera stream token here would
        open every printer's live stream to whoever sees the notification."""
        with (
            patch(
                "backend.app.services.notification_service.save_notification_photo", return_value="first_layer_a.jpg"
            ) as mock_save,
            patch("backend.app.core.auth.create_camera_stream_token", new_callable=AsyncMock) as mock_token,
        ):
            result = await service._get_or_build_photo_url(AsyncMock(), b"jpeg", "first_layer_complete")

        assert result == "https://bambuddy.example/api/v1/notifications/photos/first_layer_a.jpg"
        mock_save.assert_called_once_with(b"jpeg", "first_layer_complete")
        mock_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_print_complete_gets_its_own_photo_not_the_archive_url(self, service, external_url):
        """The archive's finish_photo_url needs a media token, so with auth on
        HA/Bark/Slack would get a 401 on it. Print Complete must build its own."""
        with patch("backend.app.services.notification_service.save_notification_photo", return_value="p.jpg"):
            result = await service._get_or_build_photo_url(AsyncMock(), b"jpeg", "print_complete")

        assert result == "https://bambuddy.example/api/v1/notifications/photos/p.jpg"

    @pytest.mark.asyncio
    async def test_cache_is_filled_and_then_reused(self, service, external_url):
        photo_cache: dict = {}

        with patch(
            "backend.app.services.notification_service.save_notification_photo", return_value="p.jpg"
        ) as mock_save:
            first = await service._get_or_build_photo_url(AsyncMock(), b"jpeg", "x", photo_cache)
            second = await service._get_or_build_photo_url(AsyncMock(), b"jpeg", "x", photo_cache)

        assert first == second == photo_cache["url"]
        mock_save.assert_called_once()


class TestNotificationPhotoRoute:
    """GET /notifications/photos/{filename} is public: the filename is the auth."""

    @pytest.mark.asyncio
    async def test_serves_a_saved_photo_without_credentials(self, tmp_path, monkeypatch):
        from backend.app.api.routes.notifications import get_notification_photo

        monkeypatch.setattr(notification_photos.settings, "base_dir", tmp_path)
        filename = notification_photos.save_notification_photo(b"\xff\xd8jpeg", "test")

        response = await get_notification_photo(filename)

        assert response.media_type == "image/jpeg"
        assert response.headers["cache-control"] == "private, no-store"

    @pytest.mark.asyncio
    async def test_unknown_name_is_404(self, tmp_path, monkeypatch):
        from fastapi import HTTPException

        from backend.app.api.routes.notifications import get_notification_photo

        monkeypatch.setattr(notification_photos.settings, "base_dir", tmp_path)

        with pytest.raises(HTTPException) as exc:
            await get_notification_photo("test_20260930_120000_" + "A" * 32 + ".jpg")

        assert exc.value.status_code == 404


class TestSendEmailInlineImage:
    @pytest.fixture
    def smtp_settings(self):
        return SimpleNamespace(
            smtp_from_name="Bambuddy",
            smtp_from_email="bambuddy@example.com",
            smtp_security="none",
            smtp_auth_enabled=False,
            smtp_username=None,
            smtp_password=None,
            smtp_host="smtp.example.com",
            smtp_port=25,
        )

    def _sent_message(self, smtp_settings, **kwargs):
        with patch("backend.app.services.email_service.smtplib.SMTP") as mock_smtp:
            send_email(smtp_settings, "to@example.com", "Subject", "text", **kwargs)
        return mock_smtp.return_value.__enter__.return_value.send_message.call_args[0][0]

    def test_image_with_html_becomes_multipart_related(self, smtp_settings):
        msg = self._sent_message(
            smtp_settings, body_html='<img src="cid:photo">', image_data=b"\xff\xd8jpeg", image_cid="photo"
        )

        assert msg.get_content_type() == "multipart/related"
        alternative, image = msg.get_payload()
        assert alternative.get_content_type() == "multipart/alternative"
        assert image.get_content_type() == "image/jpeg"
        assert image["Content-ID"] == "<photo>"
        assert image.get_payload(decode=True) == b"\xff\xd8jpeg"

    def test_image_without_html_is_dropped(self, smtp_settings):
        """No HTML part means nowhere to reference the cid, so no attachment."""
        msg = self._sent_message(smtp_settings, image_data=b"jpeg")

        assert msg.get_content_type() == "multipart/alternative"
        assert [part.get_content_type() for part in msg.get_payload()] == ["text/plain"]
