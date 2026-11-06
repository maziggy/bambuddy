"""Unit tests for email service.

These tests verify email template rendering, HTML formatting,
password generation, and SMTP settings persistence.
"""

import string
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.notification_template import NotificationTemplate
from backend.app.services.email_service import (
    create_welcome_email_from_template,
    generate_secure_password,
    render_template,
)


class TestEmailTemplateFormatting:
    """Tests for email template formatting."""

    @pytest.mark.asyncio
    async def test_welcome_email_newlines_converted_to_br(self):
        """Verify that newlines in welcome email body are converted to <br> tags."""
        # Mock database session
        db = AsyncMock(spec=AsyncSession)

        # Mock template with newlines
        template = NotificationTemplate(
            event_type="user_created",
            name="Welcome Email",
            title_template="Welcome to {app_name}",
            body_template="Hello {username}!\n\nYour password is: {password}\n\nPlease login at: {login_url}",
            is_default=True,
        )

        # Patch get_notification_template to return our template
        with patch("backend.app.services.email_service.get_notification_template", return_value=template):
            # Generate email
            subject, text_body, html_body = await create_welcome_email_from_template(
                db=db,
                username="testuser",
                password="testpass123",
                login_url="http://example.com/login",
                app_name="TestApp",
            )

        # Verify subject
        assert subject == "Welcome to TestApp"

        # Verify text body has newlines
        assert "\n\n" in text_body
        assert "Hello testuser!" in text_body
        # The password is added by Bambuddy, never by the template
        assert "Your password is: \n" in text_body
        assert text_body.endswith("Username: testuser\nPassword: testpass123")

        # Verify HTML body has <br> tags instead of relying on CSS
        assert "<br>" in html_body
        # Should not use white-space: pre-wrap
        assert "white-space: pre-wrap" not in html_body
        # Should have proper structure
        assert "<!DOCTYPE html>" in html_body
        assert '<div style="font-size: 16px;">' in html_body

        # Verify that escaped content is present (XSS protection)
        assert "Hello testuser!<br>" in html_body
        assert "Your password is: <br>" in html_body
        assert ">testpass123</code>" in html_body

    @pytest.mark.asyncio
    async def test_email_header_padding(self):
        """Verify that email header has proper padding to prevent cutoff."""
        # Mock database session
        db = AsyncMock(spec=AsyncSession)

        # Mock template
        template = NotificationTemplate(
            event_type="user_created",
            name="Welcome Email",
            title_template="Welcome",
            body_template="Test body",
            is_default=True,
        )

        # Patch get_notification_template to return our template
        with patch("backend.app.services.email_service.get_notification_template", return_value=template):
            # Generate email
            subject, text_body, html_body = await create_welcome_email_from_template(
                db=db,
                username="testuser",
                password="testpass123",
                login_url="http://example.com/login",
            )

        # Verify header has 30px padding (not 20px which was cutting off)
        assert "padding: 30px; border-radius: 8px 8px 0 0;" in html_body

    @pytest.mark.asyncio
    async def test_email_xss_protection(self):
        """Verify that HTML escaping is applied to prevent XSS attacks."""
        # Mock database session
        db = AsyncMock(spec=AsyncSession)

        # Mock template with potential XSS content
        template = NotificationTemplate(
            event_type="user_created",
            name="Welcome Email",
            title_template="Welcome <script>alert('xss')</script>",
            body_template="Hello <script>alert('xss')</script>\nTest",
            is_default=True,
        )

        # Patch get_notification_template to return our template
        with patch("backend.app.services.email_service.get_notification_template", return_value=template):
            # Generate email
            subject, text_body, html_body = await create_welcome_email_from_template(
                db=db,
                username="testuser",
                password="testpass123",
                login_url="http://example.com/login",
            )

        # Verify that script tags are escaped
        assert "&lt;script&gt;" in html_body
        # Verify no unescaped script tags
        assert "<script>" not in html_body


class TestGenerateSecurePassword:
    """Tests for generate_secure_password()."""

    def test_password_default_length(self):
        """Default password is 16 characters."""
        password = generate_secure_password()
        assert len(password) == 16

    def test_password_custom_length(self):
        """Custom length is respected."""
        password = generate_secure_password(24)
        assert len(password) == 24

    def test_password_has_required_char_types(self):
        """Password contains uppercase, lowercase, digit, and special character."""
        # Run multiple times to reduce flakiness from random shuffling
        for _ in range(5):
            password = generate_secure_password()
            assert any(c in string.ascii_uppercase for c in password), "Missing uppercase"
            assert any(c in string.ascii_lowercase for c in password), "Missing lowercase"
            assert any(c in string.digits for c in password), "Missing digit"
            assert any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password), "Missing special"


class TestRenderTemplate:
    """Tests for render_template()."""

    def test_render_template_basic(self):
        """Placeholders are replaced correctly."""
        result = render_template("Hello {name}, welcome to {app}!", {"name": "Alice", "app": "BamBuddy"})
        assert result == "Hello Alice, welcome to BamBuddy!"

    def test_render_template_removes_unreplaced(self):
        """Unreplaced placeholders are removed."""
        result = render_template("Hello {name}, your code is {code}", {"name": "Bob"})
        assert result == "Hello Bob, your code is "


class TestSMTPSettingsPersistence:
    """Tests for save_smtp_settings() and get_smtp_settings() round-trip."""

    @pytest.mark.asyncio
    async def test_save_and_retrieve_smtp_settings(self, db_session):
        """Save SMTP settings, then retrieve them and verify values match."""
        from backend.app.schemas.auth import SMTPSettings
        from backend.app.services.email_service import get_smtp_settings, save_smtp_settings

        settings = SMTPSettings(
            smtp_host="mail.example.com",
            smtp_port=465,
            smtp_username="user@example.com",
            smtp_password="secret",
            smtp_security="ssl",
            smtp_auth_enabled=True,
            smtp_from_email="noreply@example.com",
        )
        await save_smtp_settings(db_session, settings)
        await db_session.commit()

        retrieved = await get_smtp_settings(db_session)
        assert retrieved is not None
        assert retrieved.smtp_host == "mail.example.com"
        assert retrieved.smtp_port == 465
        assert retrieved.smtp_username == "user@example.com"
        assert retrieved.smtp_password == "secret"
        assert retrieved.smtp_security == "ssl"
        assert retrieved.smtp_auth_enabled is True
        assert retrieved.smtp_from_email == "noreply@example.com"

    @pytest.mark.asyncio
    async def test_get_smtp_settings_returns_none_when_unconfigured(self, db_session):
        """Empty DB returns None for SMTP settings."""
        from backend.app.services.email_service import get_smtp_settings

        result = await get_smtp_settings(db_session)
        assert result is None
