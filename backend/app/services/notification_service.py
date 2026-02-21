"""Notification service for sending push notifications via various providers."""

import asyncio
import json
import logging
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.notification import NotificationDigestQueue, NotificationLog, NotificationProvider
from backend.app.models.notification_template import NotificationTemplate

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending notifications through various providers."""

    def __init__(self):
        self._http_client: httpx.AsyncClient | None = None
        self._template_cache: dict[str, NotificationTemplate] = {}
        self._digest_scheduler_task: asyncio.Task | None = None
        self._last_digest_check: str = ""  # "HH:MM" to avoid duplicate checks

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self):
        """Close HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def _is_in_quiet_hours(self, provider: NotificationProvider) -> bool:
        """Check if current time is within provider's quiet hours."""
        if not provider.quiet_hours_enabled:
            return False

        if not provider.quiet_hours_start or not provider.quiet_hours_end:
            return False

        try:
            now = datetime.now()
            current_time = now.hour * 60 + now.minute

            start_parts = provider.quiet_hours_start.split(":")
            end_parts = provider.quiet_hours_end.split(":")

            start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
            end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

            # Handle overnight quiet hours (e.g., 22:00 to 07:00)
            if start_minutes > end_minutes:
                # Quiet hours span midnight
                return current_time >= start_minutes or current_time < end_minutes
            else:
                # Same day quiet hours
                return start_minutes <= current_time < end_minutes
        except (ValueError, TypeError, AttributeError):
            logger.warning("Invalid quiet hours format for provider %s", provider.name)
            return False

    async def _get_template(self, db: AsyncSession, event_type: str) -> NotificationTemplate | None:
        """Get a notification template by event type."""
        # Check cache first
        if event_type in self._template_cache:
            return self._template_cache[event_type]

        result = await db.execute(select(NotificationTemplate).where(NotificationTemplate.event_type == event_type))
        template = result.scalar_one_or_none()

        if template:
            self._template_cache[event_type] = template

        return template

    def _render_template(self, template_str: str, variables: dict[str, Any]) -> str:
        """Render a template string with variables. Missing variables become empty."""
        result = template_str
        for key, value in variables.items():
            result = result.replace("{" + key + "}", str(value) if value is not None else "")
        # Remove any remaining unreplaced placeholders
        result = re.sub(r"\{[a-z_]+\}", "", result)
        return result

    def _format_duration(self, seconds: int | None) -> str:
        """Format duration in seconds to human-readable string."""
        if seconds is None:
            return "Unknown"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def _clean_filename(self, filename: str) -> str:
        """Extract filename and remove file extensions."""
        import os

        # Strip path prefix (e.g., /data/Metadata/plate_5.gcode -> plate_5.gcode)
        filename = os.path.basename(filename)
        # Remove common extensions
        if filename.endswith(".gcode.3mf"):
            return filename[:-10]
        elif filename.endswith(".gcode"):
            return filename[:-6]
        elif filename.endswith(".3mf"):
            return filename[:-4]
        return filename

    async def _build_message_from_template(
        self, db: AsyncSession, event_type: str, variables: dict[str, Any]
    ) -> tuple[str, str]:
        """Build notification title and body from template."""
        # Add common variables
        variables["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        variables["app_name"] = "Bambuddy"

        template = await self._get_template(db, event_type)
        if not template:
            # Fallback to simple message
            logger.warning("Template not found for event type: %s", event_type)
            return event_type.replace("_", " ").title(), str(variables)

        title = self._render_template(template.title_template, variables)
        body = self._render_template(template.body_template, variables)

        return title, body

    async def send_test_notification(
        self, provider_type: str, config: dict[str, Any], db: AsyncSession | None = None
    ) -> tuple[bool, str]:
        """Send a test notification to verify configuration."""
        if db:
            title, message = await self._build_message_from_template(db, "test", {})
        else:
            title = "Bambuddy Test"
            message = "This is a test notification. If you see this, notifications are working!"

        try:
            if provider_type == "callmebot":
                return await self._send_callmebot(config, f"{title}\n{message}")
            elif provider_type == "ntfy":
                return await self._send_ntfy(config, title, message)
            elif provider_type == "pushover":
                return await self._send_pushover(config, title, message)
            elif provider_type == "telegram":
                return await self._send_telegram(config, f"*{title}*\n{message}")
            elif provider_type == "email":
                return await self._send_email(config, title, message)
            elif provider_type == "discord":
                return await self._send_discord(config, title, message)
            elif provider_type == "webhook":
                return await self._send_webhook(config, title, message)
            else:
                return False, f"Unknown provider type: {provider_type}"
        except Exception as e:
            logger.exception("Error sending test notification via %s", provider_type)
            return False, str(e)

    async def _send_callmebot(self, config: dict, message: str) -> tuple[bool, str]:
        """Send notification via CallMeBot (WhatsApp)."""
        phone = config.get("phone", "").strip()
        apikey = config.get("apikey", "").strip()

        if not phone or not apikey:
            return False, "Phone number and API key are required"

        # URL encode the message
        encoded_message = quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded_message}&apikey={apikey}"

        client = await self._get_client()
        response = await client.get(url)

        if response.status_code == 200:
            return True, "Message sent successfully"
        else:
            return False, f"HTTP {response.status_code}: {response.text[:200]}"

    async def _send_ntfy(
        self, config: dict, title: str, message: str, image_data: bytes | None = None
    ) -> tuple[bool, str]:
        """Send notification via ntfy."""
        server = config.get("server", "https://ntfy.sh").rstrip("/")
        topic = config.get("topic", "").strip()
        auth_token = config.get("auth_token", "").strip()

        if not topic:
            return False, "Topic is required"

        url = f"{server}/{topic}"
        headers = {"Title": title}

        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        client = await self._get_client()

        if image_data:
            # ntfy supports image attachments via multipart form-data.
            # HTTP headers cannot contain newlines, but ntfy interprets
            # literal \n (backslash-n) as newlines in the Message header.
            headers["Filename"] = "photo.jpg"
            headers["Message"] = message.replace("\n", "\\n")
            response = await client.put(url, content=image_data, headers=headers)

            if response.status_code == 400 and "attachments not allowed" in response.text:
                # Server has attachments disabled — retry without the image
                headers.pop("Filename", None)
                headers.pop("Message", None)
                response = await client.post(url, content=message, headers=headers)
        else:
            response = await client.post(url, content=message, headers=headers)

        if response.status_code in (200, 204):
            return True, "Message sent successfully"
        else:
            return False, f"HTTP {response.status_code}: {response.text[:200]}"

    async def _send_pushover(
        self, config: dict, title: str, message: str, image_data: bytes | None = None
    ) -> tuple[bool, str]:
        """Send notification via Pushover.

        Args:
            config: Provider configuration with user_key, app_token, priority
            title: Notification title
            message: Notification body
            image_data: Optional JPEG image bytes to attach (max 2.5MB)
        """
        user_key = config.get("user_key", "").strip()
        app_token = config.get("app_token", "").strip()
        priority = config.get("priority", 0)

        if not user_key or not app_token:
            return False, "User key and app token are required"

        url = "https://api.pushover.net/1/messages.json"
        data = {
            "token": app_token,
            "user": user_key,
            "title": title,
            "message": message,
            "priority": priority,
        }

        client = await self._get_client()

        if image_data:
            # Pushover supports image attachments via multipart form-data
            files = {"attachment": ("photo.jpg", image_data, "image/jpeg")}
            response = await client.post(url, data=data, files=files)
        else:
            response = await client.post(url, data=data)

        if response.status_code == 200:
            return True, "Message sent successfully"
        else:
            try:
                error_data = response.json()
                errors = error_data.get("errors", [])
                return False, f"Pushover error: {', '.join(errors)}"
            except Exception:
                return False, f"HTTP {response.status_code}: {response.text[:200]}"

    async def _send_telegram(self, config: dict, message: str, image_data: bytes | None = None) -> tuple[bool, str]:
        """Send notification via Telegram bot."""
        bot_token = config.get("bot_token", "").strip()
        chat_id = config.get("chat_id", "").strip()

        if not bot_token or not chat_id:
            return False, "Bot token and chat ID are required"

        # Escape underscores in the message body so Telegram Markdown
        # parsing doesn't break on job names like "A1_plate_8" or error
        # codes like "0300_0001".  The title is already wrapped in *bold*
        # markers, so only escape after the first newline.
        if "\n" in message:
            title_part, body_part = message.split("\n", 1)
            body_part = body_part.replace("_", "\\_")
            message = f"{title_part}\n{body_part}"

        client = await self._get_client()

        if image_data:
            # Use sendPhoto to attach the thumbnail with the caption
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            response = await client.post(
                url,
                data={"chat_id": chat_id, "caption": message, "parse_mode": "Markdown"},
                files={"photo": ("photo.jpg", image_data, "image/jpeg")},
            )
        else:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }
            response = await client.post(url, json=data)

        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                return True, "Message sent successfully"
            else:
                return False, f"Telegram error: {result.get('description', 'Unknown error')}"
        else:
            return False, f"HTTP {response.status_code}: {response.text[:200]}"

    async def _send_email(self, config: dict, subject: str, body: str) -> tuple[bool, str]:
        """Send notification via email (SMTP)."""
        smtp_server = config.get("smtp_server", "").strip()
        smtp_port = int(config.get("smtp_port", 587))
        username = config.get("username", "").strip()
        password = config.get("password", "").strip()
        from_email = config.get("from_email", "").strip()
        to_email = config.get("to_email", "").strip()
        # Security: "starttls" (port 587), "ssl" (port 465), "none" (port 25)
        security = config.get("security", "starttls")
        # Authentication: "true" or "false"
        auth_enabled = config.get("auth_enabled", "true").lower() == "true"

        if not all([smtp_server, from_email, to_email]):
            return False, "SMTP server, from email, and to email are required"

        if auth_enabled and not all([username, password]):
            return False, "Username and password are required when authentication is enabled"

        try:
            msg = MIMEMultipart()
            msg["From"] = from_email
            msg["To"] = to_email
            msg["Subject"] = f"[Bambuddy] {subject}"
            msg.attach(MIMEText(body, "plain"))

            if security == "ssl":
                # Direct SSL connection (typically port 465)
                server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            elif security == "starttls":
                # STARTTLS upgrade (typically port 587)
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
            else:
                # No encryption (typically port 25) - use with caution
                server = smtplib.SMTP(smtp_server, smtp_port)

            if auth_enabled:
                server.login(username, password)

            server.sendmail(from_email, to_email, msg.as_string())
            server.quit()

            return True, "Email sent successfully"
        except smtplib.SMTPAuthenticationError:
            return False, "SMTP authentication failed - check username/password"
        except smtplib.SMTPException as e:
            return False, f"SMTP error: {str(e)}"
        except Exception as e:
            return False, f"Email error: {str(e)}"

    async def _send_discord(
        self, config: dict, title: str, message: str, image_data: bytes | None = None
    ) -> tuple[bool, str]:
        """Send notification via Discord webhook."""
        webhook_url = config.get("webhook_url", "").strip()

        if not webhook_url:
            return False, "Webhook URL is required"

        if not webhook_url.startswith("https://discord.com/api/webhooks/"):
            return False, "Invalid Discord webhook URL"

        # Discord embed format for nicer messages
        embed = {
            "title": title,
            "description": message,
            "color": 0x00AE42,  # Bambu green
        }

        client = await self._get_client()

        if image_data:
            # Attach image via multipart form-data and reference in embed
            embed["image"] = {"url": "attachment://photo.jpg"}
            payload = {"embeds": [embed]}
            response = await client.post(
                webhook_url,
                data={"payload_json": json.dumps(payload)},
                files={"files[0]": ("photo.jpg", image_data, "image/jpeg")},
            )
        else:
            response = await client.post(webhook_url, json={"embeds": [embed]})

        if response.status_code in (200, 204):
            return True, "Message sent successfully"
        else:
            return False, f"HTTP {response.status_code}: {response.text[:200]}"

    async def _send_webhook(self, config: dict, title: str, message: str) -> tuple[bool, str]:
        """Send notification via generic webhook (POST JSON).

        Supports two payload formats:
        - generic: Custom field names with timestamp/source metadata
        - slack: Slack/Mattermost compatible format (just {"text": "..."})
        """
        webhook_url = config.get("webhook_url", "").strip()
        auth_header = config.get("auth_header", "").strip()
        payload_format = config.get("payload_format", "generic").strip()

        if not webhook_url:
            return False, "Webhook URL is required"

        # Build payload based on format
        if payload_format == "slack":
            # Slack/Mattermost format - just text field
            data = {"text": f"*{title}*\n{message}"}
        else:
            # Generic format with custom field names
            custom_field_title = config.get("field_title", "title").strip() or "title"
            custom_field_message = config.get("field_message", "message").strip() or "message"
            data = {
                custom_field_title: title,
                custom_field_message: message,
                "timestamp": datetime.now().isoformat(),
                "source": "Bambuddy",
            }

        headers = {"Content-Type": "application/json"}
        if auth_header:
            # Support "Bearer token" or just "token" format
            if " " in auth_header:
                headers["Authorization"] = auth_header
            else:
                headers["Authorization"] = f"Bearer {auth_header}"

        client = await self._get_client()
        try:
            response = await client.post(webhook_url, json=data, headers=headers)

            if response.status_code in (200, 201, 202, 204):
                return True, "Webhook delivered successfully"
            else:
                return False, f"HTTP {response.status_code}: {response.text[:200]}"
        except Exception as e:
            return False, f"Webhook error: {str(e)}"

    async def _send_to_provider(
        self,
        provider: NotificationProvider,
        title: str,
        message: str,
        db: AsyncSession | None = None,
        image_data: bytes | None = None,
    ) -> tuple[bool, str]:
        """Send notification to a specific provider."""
        # Check quiet hours
        if self._is_in_quiet_hours(provider):
            logger.info("Skipping notification to %s - quiet hours active", provider.name)
            return True, "Skipped - quiet hours"

        config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config

        try:
            if provider.provider_type == "callmebot":
                return await self._send_callmebot(config, f"{title}\n{message}")
            elif provider.provider_type == "ntfy":
                return await self._send_ntfy(config, title, message, image_data=image_data)
            elif provider.provider_type == "pushover":
                return await self._send_pushover(config, title, message, image_data=image_data)
            elif provider.provider_type == "telegram":
                return await self._send_telegram(config, f"*{title}*\n{message}", image_data=image_data)
            elif provider.provider_type == "email":
                return await self._send_email(config, title, message)
            elif provider.provider_type == "discord":
                return await self._send_discord(config, title, message, image_data=image_data)
            elif provider.provider_type == "webhook":
                return await self._send_webhook(config, title, message)
            else:
                return False, f"Unknown provider type: {provider.provider_type}"
        except Exception as e:
            logger.exception("Error sending notification via %s", provider.provider_type)
            return False, str(e)

    async def _update_provider_status(
        self, db: AsyncSession, provider_id: int, success: bool, error: str | None = None
    ):
        """Update provider status after sending notification."""
        result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
        provider = result.scalar_one_or_none()
        if provider:
            if success:
                provider.last_success = datetime.utcnow()
            else:
                provider.last_error = error
                provider.last_error_at = datetime.utcnow()
            await db.commit()

    async def _get_providers_for_event(
        self,
        db: AsyncSession,
        event_field: str,
        printer_id: int | None = None,
    ) -> list[NotificationProvider]:
        """Get all enabled providers that want a specific event type."""
        # Build the query dynamically based on event field
        query = select(NotificationProvider).where(
            NotificationProvider.enabled.is_(True),
            getattr(NotificationProvider, event_field).is_(True),
        )

        if printer_id is not None:
            query = query.where(
                (NotificationProvider.printer_id.is_(None)) | (NotificationProvider.printer_id == printer_id)
            )

        result = await db.execute(query)
        return list(result.scalars().all())

    async def _log_notification(
        self,
        db: AsyncSession,
        provider_id: int,
        event_type: str,
        title: str,
        message: str,
        success: bool,
        error_message: str | None = None,
        printer_id: int | None = None,
        printer_name: str | None = None,
    ):
        """Create a log entry for a sent notification."""
        try:
            log = NotificationLog(
                provider_id=provider_id,
                event_type=event_type,
                title=title,
                message=message,
                success=success,
                error_message=error_message,
                printer_id=printer_id,
                printer_name=printer_name,
            )
            db.add(log)
            await db.commit()
        except Exception as e:
            logger.warning("Failed to log notification: %s", e)
            # Don't fail the notification just because logging failed

    async def _send_to_providers(
        self,
        providers: list[NotificationProvider],
        title: str,
        message: str,
        db: AsyncSession,
        event_type: str = "unknown",
        printer_id: int | None = None,
        printer_name: str | None = None,
        force_immediate: bool = False,
        image_data: bytes | None = None,
    ):
        """Send notification to multiple providers and log the results.

        All notifications are always sent immediately. If digest mode is enabled,
        the notification is ALSO queued for the daily digest summary.
        """
        for provider in providers:
            try:
                # Always send notification immediately
                success, error = await self._send_to_provider(provider, title, message, db, image_data=image_data)

                # Also queue for digest if enabled (digest is a summary, not a queue)
                if provider.daily_digest_enabled and provider.daily_digest_time:
                    await self._queue_for_digest(
                        provider=provider,
                        event_type=event_type,
                        title=title,
                        message=message,
                        db=db,
                        printer_id=printer_id,
                        printer_name=printer_name,
                    )
                await self._update_provider_status(db, provider.id, success, error if not success else None)
                await self._log_notification(
                    db=db,
                    provider_id=provider.id,
                    event_type=event_type,
                    title=title,
                    message=message,
                    success=success,
                    error_message=error if not success else None,
                    printer_id=printer_id,
                    printer_name=printer_name,
                )
                if success:
                    logger.info("Sent notification via %s", provider.name)
                else:
                    logger.warning("Failed to send notification via %s: %s", provider.name, error)
            except Exception as e:
                logger.exception("Error sending notification via %s", provider.name)
                await self._update_provider_status(db, provider.id, False, str(e))
                await self._log_notification(
                    db=db,
                    provider_id=provider.id,
                    event_type=event_type,
                    title=title,
                    message=message,
                    success=False,
                    error_message=str(e),
                    printer_id=printer_id,
                    printer_name=printer_name,
                )

    async def on_print_start(
        self,
        printer_id: int,
        printer_name: str,
        data: dict,
        db: AsyncSession,
        archive_data: dict | None = None,
    ):
        """Handle print start event - send notifications to relevant providers.

        Args:
            printer_id: The printer ID
            printer_name: The printer name
            data: MQTT event data with filename, subtask_name, remaining_time, raw_data
            db: Database session
            archive_data: Optional archive data with print_time_seconds from 3MF parsing
        """
        logger.info("on_print_start called for printer %s (%s)", printer_id, printer_name)
        providers = await self._get_providers_for_event(db, "on_print_start", printer_id)
        if not providers:
            logger.info("No notification providers configured for print_start event on printer %s", printer_id)
            return

        # Use subtask_name (project name) if available, otherwise use filename
        subtask_name = data.get("subtask_name")
        if subtask_name:
            # Replace underscores with spaces for readability
            filename = subtask_name.replace("_", " ")
        else:
            filename = self._clean_filename(data.get("filename", "Unknown"))

        # Priority for estimated_time:
        # 1. Archive's print_time_seconds from 3MF parsing (most reliable)
        # 2. MQTT remaining_time (may be 0 at print start)
        # 3. raw_data mc_remaining_time
        estimated_time = None

        # Try archive data first (from 3MF parsing - most reliable)
        if archive_data and archive_data.get("print_time_seconds"):
            estimated_time = archive_data["print_time_seconds"]
            logger.debug("Using print_time_seconds from archive: %s", estimated_time)

        # Fall back to MQTT remaining_time
        if estimated_time is None:
            estimated_time = data.get("remaining_time")
            if estimated_time:
                logger.debug("Using remaining_time from MQTT: %s", estimated_time)

        # Last resort: raw_data mc_remaining_time (in minutes, convert to seconds)
        if estimated_time is None:
            raw_time = data.get("raw_data", {}).get("mc_remaining_time")
            if raw_time:
                estimated_time = raw_time * 60
                logger.debug("Using mc_remaining_time from raw_data: %s", estimated_time)

        time_str = self._format_duration(estimated_time)

        variables = {
            "printer": printer_name,
            "filename": filename,
            "estimated_time": time_str,
        }

        # Extract image data for providers that support attachments (e.g. Pushover)
        image_data = None
        if archive_data:
            image_data = archive_data.get("image_data")

        logger.info("Found %s providers for print_start: %s", len(providers), [p.name for p in providers])
        title, message = await self._build_message_from_template(db, "print_start", variables)
        await self._send_to_providers(
            providers, title, message, db, "print_start", printer_id, printer_name, image_data=image_data
        )

    async def on_print_complete(
        self,
        printer_id: int,
        printer_name: str,
        status: str,
        data: dict,
        db: AsyncSession,
        archive_data: dict | None = None,
    ):
        """Handle print complete event - send notifications to relevant providers."""
        logger.info("on_print_complete called for printer %s (%s), status=%s", printer_id, printer_name, status)

        # Determine event type based on status
        if status == "completed":
            event_field = "on_print_complete"
            event_type = "print_complete"
        elif status in ("failed",):
            event_field = "on_print_failed"
            event_type = "print_failed"
        elif status in ("aborted", "stopped", "cancelled"):
            event_field = "on_print_stopped"
            event_type = "print_stopped"
        else:
            logger.warning("Unknown print status '%s', defaulting to on_print_complete", status)
            event_field = "on_print_complete"
            event_type = "print_complete"

        providers = await self._get_providers_for_event(db, event_field, printer_id)
        if not providers:
            logger.info("No notification providers configured for %s event on printer %s", event_field, printer_id)
            return

        # Use subtask_name (project name) if available, otherwise use filename
        subtask_name = data.get("subtask_name")
        if subtask_name:
            filename = subtask_name.replace("_", " ")
        else:
            filename = self._clean_filename(data.get("filename", "Unknown"))

        variables = {
            "printer": printer_name,
            "filename": filename,
            "duration": "Unknown",
            "filament_grams": "Unknown",
            "reason": "Unknown",
        }

        if archive_data:
            if archive_data.get("print_time_seconds"):
                variables["duration"] = self._format_duration(archive_data["print_time_seconds"])
            if archive_data.get("actual_filament_grams"):
                variables["filament_grams"] = f"{archive_data['actual_filament_grams']:.1f}"
            if status == "failed" and archive_data.get("failure_reason"):
                variables["reason"] = archive_data["failure_reason"]
            if archive_data.get("finish_photo_url"):
                variables["finish_photo_url"] = archive_data["finish_photo_url"]

            # Build per-slot breakdown string with AMS info when available
            if archive_data.get("usage_results"):
                parts = []
                for u in archive_data["usage_results"]:
                    ams_id = u.get("ams_id", 0)
                    tray_id = u.get("tray_id", 0)
                    material = u.get("material", "Unknown") or "Unknown"
                    used = u.get("weight_used", 0)
                    if ams_id >= 128:
                        slot_label = "Ext"
                    else:
                        slot_label = f"AMS-{chr(65 + ams_id)} T{tray_id + 1}"
                    parts.append(f"{slot_label} {material}: {used:.1f}g")
                variables["filament_details"] = " | ".join(parts)
            elif archive_data.get("filament_slots"):
                parts = []
                for slot in archive_data["filament_slots"]:
                    ftype = slot.get("type", "Unknown") or "Unknown"
                    used = slot.get("used_g", 0)
                    parts.append(f"{ftype}: {used:.1f}g")
                variables["filament_details"] = " | ".join(parts)

            # Add progress for partial prints
            if archive_data.get("progress") is not None:
                variables["progress"] = str(archive_data["progress"])

        # Extract image data for providers that support attachments (e.g. Pushover)
        image_data = None
        if archive_data:
            image_data = archive_data.get("image_data")

        logger.info("Found %s providers for %s: %s", len(providers), event_field, [p.name for p in providers])
        title, message = await self._build_message_from_template(db, event_type, variables)
        await self._send_to_providers(
            providers, title, message, db, event_type, printer_id, printer_name, image_data=image_data
        )

    async def on_print_progress(
        self,
        printer_id: int,
        printer_name: str,
        filename: str,
        progress: int,
        db: AsyncSession,
        remaining_time: int | None = None,
        image_data: bytes | None = None,
    ):
        """Handle print progress milestone (25%, 50%, 75%)."""
        providers = await self._get_providers_for_event(db, "on_print_progress", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "filename": self._clean_filename(filename),
            "progress": str(progress),
            "remaining_time": self._format_duration(remaining_time) if remaining_time else "Unknown",
        }

        title, message = await self._build_message_from_template(db, "print_progress", variables)
        await self._send_to_providers(
            providers, title, message, db, "print_progress", printer_id, printer_name, image_data=image_data
        )

    async def on_printer_offline(self, printer_id: int, printer_name: str, db: AsyncSession):
        """Handle printer offline event."""
        providers = await self._get_providers_for_event(db, "on_printer_offline", printer_id)
        if not providers:
            return

        variables = {"printer": printer_name}

        title, message = await self._build_message_from_template(db, "printer_offline", variables)
        await self._send_to_providers(providers, title, message, db, "printer_offline", printer_id, printer_name)

    async def on_printer_error(
        self,
        printer_id: int,
        printer_name: str,
        error_type: str,
        db: AsyncSession,
        error_detail: str | None = None,
        image_data: bytes | None = None,
    ):
        """Handle printer error event (AMS issues, etc.)."""
        providers = await self._get_providers_for_event(db, "on_printer_error", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "error_type": error_type,
            "error_detail": error_detail or "No details available",
        }

        title, message = await self._build_message_from_template(db, "printer_error", variables)
        await self._send_to_providers(
            providers, title, message, db, "printer_error", printer_id, printer_name, image_data=image_data
        )

    async def on_plate_not_empty(
        self,
        printer_id: int,
        printer_name: str,
        db: AsyncSession,
        difference_percent: float | None = None,
    ):
        """Handle plate not empty event - objects detected on build plate before print."""
        providers = await self._get_providers_for_event(db, "on_plate_not_empty", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "difference_percent": f"{difference_percent:.1f}" if difference_percent else "N/A",
        }

        title, message = await self._build_message_from_template(db, "plate_not_empty", variables)
        await self._send_to_providers(
            providers, title, message, db, "plate_not_empty", printer_id, printer_name, force_immediate=True
        )

    async def on_filament_low(
        self,
        printer_id: int,
        printer_name: str,
        slot: int,
        remaining_percent: int,
        db: AsyncSession,
        color: str | None = None,
    ):
        """Handle low filament event."""
        providers = await self._get_providers_for_event(db, "on_filament_low", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "slot": str(slot),
            "remaining_percent": str(remaining_percent),
            "color": color or "",
        }

        title, message = await self._build_message_from_template(db, "filament_low", variables)
        await self._send_to_providers(providers, title, message, db, "filament_low", printer_id, printer_name)

    async def on_maintenance_due(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_items: list[dict],
        db: AsyncSession,
    ):
        """Handle maintenance due event - sends notification when maintenance is due or warning."""
        if not maintenance_items:
            return

        providers = await self._get_providers_for_event(db, "on_maintenance_due", printer_id)
        if not providers:
            logger.info("No notification providers configured for maintenance_due event on printer %s", printer_id)
            return

        # Format maintenance items list
        items_list = []
        for item in maintenance_items:
            status = "OVERDUE" if item.get("is_due") else "Soon"
            items_list.append(f"- {item['name']} ({status})")
        items_str = "\n".join(items_list)

        variables = {
            "printer": printer_name,
            "items": items_str,
        }

        logger.info("Found %s providers for maintenance_due: %s", len(providers), [p.name for p in providers])
        title, message = await self._build_message_from_template(db, "maintenance_due", variables)
        await self._send_to_providers(providers, title, message, db, "maintenance_due", printer_id, printer_name)

    async def on_ams_humidity_high(
        self,
        printer_id: int,
        printer_name: str,
        ams_label: str,
        humidity: float,
        threshold: float,
        db: AsyncSession,
    ):
        """Handle AMS high humidity alarm event. Always sends immediately (bypasses digest)."""
        providers = await self._get_providers_for_event(db, "on_ams_humidity_high", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "ams_label": ams_label,
            "humidity": f"{humidity:.0f}",
            "threshold": f"{threshold:.0f}",
        }

        title, message = await self._build_message_from_template(db, "ams_humidity_high", variables)
        # Alarms always send immediately, bypassing digest mode
        await self._send_to_providers(
            providers, title, message, db, "ams_humidity_high", printer_id, printer_name, force_immediate=True
        )

    async def on_ams_temperature_high(
        self,
        printer_id: int,
        printer_name: str,
        ams_label: str,
        temperature: float,
        threshold: float,
        db: AsyncSession,
    ):
        """Handle AMS high temperature alarm event. Always sends immediately (bypasses digest)."""
        providers = await self._get_providers_for_event(db, "on_ams_temperature_high", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "ams_label": ams_label,
            "temperature": f"{temperature:.1f}",
            "threshold": f"{threshold:.1f}",
        }

        title, message = await self._build_message_from_template(db, "ams_temperature_high", variables)
        # Alarms always send immediately, bypassing digest mode
        await self._send_to_providers(
            providers, title, message, db, "ams_temperature_high", printer_id, printer_name, force_immediate=True
        )

    async def on_ams_ht_humidity_high(
        self,
        printer_id: int,
        printer_name: str,
        ams_label: str,
        humidity: float,
        threshold: float,
        db: AsyncSession,
    ):
        """Handle AMS-HT high humidity alarm event. Always sends immediately (bypasses digest)."""
        providers = await self._get_providers_for_event(db, "on_ams_ht_humidity_high", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "ams_label": ams_label,
            "humidity": f"{humidity:.0f}",
            "threshold": f"{threshold:.0f}",
        }

        # Use the same template as regular AMS (can create separate templates later if needed)
        title, message = await self._build_message_from_template(db, "ams_humidity_high", variables)
        # Alarms always send immediately, bypassing digest mode
        await self._send_to_providers(
            providers, title, message, db, "ams_ht_humidity_high", printer_id, printer_name, force_immediate=True
        )

    async def on_ams_ht_temperature_high(
        self,
        printer_id: int,
        printer_name: str,
        ams_label: str,
        temperature: float,
        threshold: float,
        db: AsyncSession,
    ):
        """Handle AMS-HT high temperature alarm event. Always sends immediately (bypasses digest)."""
        providers = await self._get_providers_for_event(db, "on_ams_ht_temperature_high", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "ams_label": ams_label,
            "temperature": f"{temperature:.1f}",
            "threshold": f"{threshold:.1f}",
        }

        # Use the same template as regular AMS (can create separate templates later if needed)
        title, message = await self._build_message_from_template(db, "ams_temperature_high", variables)
        # Alarms always send immediately, bypassing digest mode
        await self._send_to_providers(
            providers, title, message, db, "ams_ht_temperature_high", printer_id, printer_name, force_immediate=True
        )

    async def on_bed_cooled(
        self,
        printer_id: int,
        printer_name: str,
        bed_temp: float,
        threshold: float,
        filename: str,
        db: AsyncSession,
    ):
        """Handle bed cooled event - bed temperature dropped below threshold after print."""
        providers = await self._get_providers_for_event(db, "on_bed_cooled", printer_id)
        if not providers:
            return

        variables = {
            "printer": printer_name,
            "bed_temp": f"{bed_temp:.0f}",
            "threshold": f"{threshold:.0f}",
            "filename": self._clean_filename(filename) if filename else "Unknown",
        }

        title, message = await self._build_message_from_template(db, "bed_cooled", variables)
        await self._send_to_providers(providers, title, message, db, "bed_cooled", printer_id, printer_name)

    def clear_template_cache(self):
        """Clear the template cache. Call this when templates are updated."""
        self._template_cache.clear()

    # ==================== Queue Notifications ====================

    async def on_queue_job_added(
        self,
        job_name: str,
        target: str,
        db: AsyncSession,
        printer_id: int | None = None,
        printer_name: str | None = None,
    ):
        """Handle queue job added event."""
        providers = await self._get_providers_for_event(db, "on_queue_job_added", printer_id)
        if not providers:
            return

        variables = {
            "job_name": job_name,
            "target": target,  # e.g., "Printer1" or "Any X1C"
            "printer": printer_name or target,
        }

        title, message = await self._build_message_from_template(db, "queue_job_added", variables)
        await self._send_to_providers(providers, title, message, db, "queue_job_added", printer_id, printer_name)

    async def on_queue_job_assigned(
        self,
        job_name: str,
        printer_id: int,
        printer_name: str,
        target_model: str,
        db: AsyncSession,
    ):
        """Handle model-based job assigned to printer event."""
        providers = await self._get_providers_for_event(db, "on_queue_job_assigned", printer_id)
        if not providers:
            return

        variables = {
            "job_name": job_name,
            "printer": printer_name,
            "target_model": target_model,
        }

        title, message = await self._build_message_from_template(db, "queue_job_assigned", variables)
        await self._send_to_providers(providers, title, message, db, "queue_job_assigned", printer_id, printer_name)

    async def on_queue_job_started(
        self,
        job_name: str,
        printer_id: int,
        printer_name: str,
        db: AsyncSession,
        estimated_time: int | None = None,
    ):
        """Handle queue job started printing event."""
        providers = await self._get_providers_for_event(db, "on_queue_job_started", printer_id)
        if not providers:
            return

        variables = {
            "job_name": job_name,
            "printer": printer_name,
            "estimated_time": self._format_duration(estimated_time),
        }

        title, message = await self._build_message_from_template(db, "queue_job_started", variables)
        await self._send_to_providers(providers, title, message, db, "queue_job_started", printer_id, printer_name)

    async def on_queue_job_waiting(
        self,
        job_name: str,
        target_model: str,
        waiting_reason: str,
        db: AsyncSession,
    ):
        """Handle job waiting for filament event."""
        providers = await self._get_providers_for_event(db, "on_queue_job_waiting", None)
        if not providers:
            return

        variables = {
            "job_name": job_name,
            "target_model": target_model,
            "waiting_reason": waiting_reason,
        }

        title, message = await self._build_message_from_template(db, "queue_job_waiting", variables)
        await self._send_to_providers(providers, title, message, db, "queue_job_waiting")

    async def on_queue_job_skipped(
        self,
        job_name: str,
        printer_id: int,
        printer_name: str,
        reason: str,
        db: AsyncSession,
    ):
        """Handle job skipped event (e.g., previous print failed)."""
        providers = await self._get_providers_for_event(db, "on_queue_job_skipped", printer_id)
        if not providers:
            return

        variables = {
            "job_name": job_name,
            "printer": printer_name,
            "reason": reason,
        }

        title, message = await self._build_message_from_template(db, "queue_job_skipped", variables)
        await self._send_to_providers(providers, title, message, db, "queue_job_skipped", printer_id, printer_name)

    async def on_queue_job_failed(
        self,
        job_name: str,
        printer_id: int | None,
        printer_name: str | None,
        reason: str,
        db: AsyncSession,
    ):
        """Handle job failed to start event (upload error, etc.)."""
        providers = await self._get_providers_for_event(db, "on_queue_job_failed", printer_id)
        if not providers:
            return

        variables = {
            "job_name": job_name,
            "printer": printer_name or "Unknown",
            "reason": reason,
        }

        title, message = await self._build_message_from_template(db, "queue_job_failed", variables)
        await self._send_to_providers(providers, title, message, db, "queue_job_failed", printer_id, printer_name)

    async def on_queue_completed(
        self,
        completed_count: int,
        db: AsyncSession,
    ):
        """Handle all queue jobs completed event."""
        providers = await self._get_providers_for_event(db, "on_queue_completed", None)
        if not providers:
            return

        variables = {
            "completed_count": str(completed_count),
        }

        title, message = await self._build_message_from_template(db, "queue_completed", variables)
        await self._send_to_providers(providers, title, message, db, "queue_completed")

    async def _queue_for_digest(
        self,
        provider: NotificationProvider,
        event_type: str,
        title: str,
        message: str,
        db: AsyncSession,
        printer_id: int | None = None,
        printer_name: str | None = None,
    ):
        """Queue a notification for later delivery in the daily digest."""
        try:
            queue_entry = NotificationDigestQueue(
                provider_id=provider.id,
                event_type=event_type,
                title=title,
                message=message,
                printer_id=printer_id,
                printer_name=printer_name,
            )
            db.add(queue_entry)
            await db.commit()
            logger.info("Queued notification for digest: %s for provider %s", event_type, provider.name)
        except Exception as e:
            logger.warning("Failed to queue notification for digest: %s", e)

    async def send_digest(self, provider_id: int):
        """Send all queued notifications as a single digest for a provider."""
        from backend.app.core.database import async_session

        async with async_session() as db:
            # Get the provider
            result = await db.execute(select(NotificationProvider).where(NotificationProvider.id == provider_id))
            provider = result.scalar_one_or_none()

            if not provider or not provider.enabled:
                return

            # Get all queued notifications for this provider
            result = await db.execute(
                select(NotificationDigestQueue)
                .where(NotificationDigestQueue.provider_id == provider_id)
                .order_by(NotificationDigestQueue.created_at)
            )
            queue_entries = list(result.scalars().all())

            if not queue_entries:
                logger.debug("No queued notifications for provider %s", provider.name)
                return

            # Build digest message
            title = f"Daily Digest - {len(queue_entries)} Events"

            # Group by event type
            events_by_type: dict[str, list] = {}
            for entry in queue_entries:
                if entry.event_type not in events_by_type:
                    events_by_type[entry.event_type] = []
                events_by_type[entry.event_type].append(entry)

            # Format the digest body
            body_parts = []
            for event_type, entries in events_by_type.items():
                event_label = event_type.replace("_", " ").title()
                body_parts.append(f"== {event_label} ({len(entries)}) ==")
                for entry in entries:
                    time_str = entry.created_at.strftime("%H:%M")
                    printer_info = f"[{entry.printer_name}] " if entry.printer_name else ""
                    body_parts.append(f"  {time_str} {printer_info}{entry.title}")
                body_parts.append("")

            body = "\n".join(body_parts)

            # Send the digest
            success, error = await self._send_to_provider(provider, title, body, db)

            # Log the digest
            await self._log_notification(
                db=db,
                provider_id=provider.id,
                event_type="daily_digest",
                title=title,
                message=body,
                success=success,
                error_message=error if not success else None,
            )

            # Clear the queue
            for entry in queue_entries:
                await db.delete(entry)
            await db.commit()

            if success:
                logger.info("Sent daily digest with %s events to %s", len(queue_entries), provider.name)
            else:
                logger.warning("Failed to send daily digest to %s: %s", provider.name, error)

    async def check_and_send_digests(self):
        """Check all providers and send digests if it's their scheduled time."""
        from backend.app.core.database import async_session

        current_time = datetime.now().strftime("%H:%M")

        # Avoid duplicate checks within the same minute
        if current_time == self._last_digest_check:
            return
        self._last_digest_check = current_time

        async with async_session() as db:
            # Find all providers with digest enabled at this time
            result = await db.execute(
                select(NotificationProvider).where(
                    NotificationProvider.enabled.is_(True),
                    NotificationProvider.daily_digest_enabled.is_(True),
                    NotificationProvider.daily_digest_time == current_time,
                )
            )
            providers = result.scalars().all()

            for provider in providers:
                try:
                    await self.send_digest(provider.id)
                except Exception as e:
                    logger.error("Error sending digest for provider %s: %s", provider.id, e)

    def start_digest_scheduler(self):
        """Start the background scheduler for daily digest notifications."""
        if self._digest_scheduler_task is None:
            self._digest_scheduler_task = asyncio.create_task(self._digest_scheduler_loop())
            logger.info("Notification digest scheduler started")

    def stop_digest_scheduler(self):
        """Stop the background scheduler for daily digests."""
        if self._digest_scheduler_task:
            self._digest_scheduler_task.cancel()
            self._digest_scheduler_task = None
            logger.info("Notification digest scheduler stopped")

    async def _digest_scheduler_loop(self):
        """Background loop that checks for scheduled digests every minute."""
        while True:
            try:
                await self.check_and_send_digests()
            except Exception as e:
                logger.error("Error in digest scheduler: %s", e)

            # Wait until the next minute
            await asyncio.sleep(60)


# Global instance
notification_service = NotificationService()
