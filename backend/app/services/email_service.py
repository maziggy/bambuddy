"""Email service for sending authentication-related emails."""

from __future__ import annotations

import logging
import secrets
import smtplib
import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.schemas.auth import SMTPSettings

logger = logging.getLogger(__name__)


def generate_secure_password(length: int = 16) -> str:
    """Generate a secure random password.
    
    Args:
        length: Length of the password (default: 16)
        
    Returns:
        A secure random password containing uppercase, lowercase, digits, and special characters
    """
    import random
    
    # Define character sets
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    
    # Ensure at least one character from each set
    password_chars = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    
    # Fill the rest with random characters from all sets
    all_chars = lowercase + uppercase + digits + special
    password_chars.extend(secrets.choice(all_chars) for _ in range(length - 4))
    
    # Shuffle to avoid predictable patterns
    random.shuffle(password_chars)
    
    return "".join(password_chars)


async def get_smtp_settings(db: AsyncSession) -> SMTPSettings | None:
    """Get SMTP settings from database.
    
    Args:
        db: Database session
        
    Returns:
        SMTPSettings object or None if not configured
    """
    # Fetch all SMTP-related settings
    result = await db.execute(
        select(Settings).where(
            Settings.key.in_([
                "smtp_host",
                "smtp_port",
                "smtp_username",
                "smtp_password",
                "smtp_use_tls",
                "smtp_from_email",
                "smtp_from_name",
            ])
        )
    )
    settings_dict = {s.key: s.value for s in result.scalars().all()}
    
    # Check if minimum required settings are present
    required_keys = ["smtp_host", "smtp_port", "smtp_username", "smtp_from_email"]
    if not all(key in settings_dict for key in required_keys):
        return None
    
    return SMTPSettings(
        smtp_host=settings_dict["smtp_host"],
        smtp_port=int(settings_dict["smtp_port"]),
        smtp_username=settings_dict["smtp_username"],
        smtp_password=settings_dict.get("smtp_password"),
        smtp_use_tls=settings_dict.get("smtp_use_tls", "true").lower() == "true",
        smtp_from_email=settings_dict["smtp_from_email"],
        smtp_from_name=settings_dict.get("smtp_from_name", "BamBuddy"),
    )


async def save_smtp_settings(db: AsyncSession, smtp_settings: SMTPSettings) -> None:
    """Save SMTP settings to database.
    
    Args:
        db: Database session
        smtp_settings: SMTP settings to save
    """
    from sqlalchemy import func
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    
    settings_data = {
        "smtp_host": smtp_settings.smtp_host,
        "smtp_port": str(smtp_settings.smtp_port),
        "smtp_username": smtp_settings.smtp_username,
        "smtp_use_tls": "true" if smtp_settings.smtp_use_tls else "false",
        "smtp_from_email": smtp_settings.smtp_from_email,
        "smtp_from_name": smtp_settings.smtp_from_name,
    }
    
    # Only save password if provided
    if smtp_settings.smtp_password:
        settings_data["smtp_password"] = smtp_settings.smtp_password
    
    for key, value in settings_data.items():
        stmt = sqlite_insert(Settings).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "updated_at": func.now()},
        )
        await db.execute(stmt)


def send_email(
    smtp_settings: SMTPSettings,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> None:
    """Send an email using SMTP.
    
    Args:
        smtp_settings: SMTP configuration
        to_email: Recipient email address
        subject: Email subject
        body_text: Plain text body
        body_html: Optional HTML body
        
    Raises:
        Exception: If email sending fails
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{smtp_settings.smtp_from_name} <{smtp_settings.smtp_from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    
    # Attach plain text part
    msg.attach(MIMEText(body_text, "plain"))
    
    # Attach HTML part if provided
    if body_html:
        msg.attach(MIMEText(body_html, "html"))
    
    # Send email
    try:
        if smtp_settings.smtp_use_tls:
            # Use TLS (port 587 typically)
            with smtplib.SMTP(smtp_settings.smtp_host, smtp_settings.smtp_port, timeout=10) as server:
                server.starttls()
                if smtp_settings.smtp_password:
                    server.login(smtp_settings.smtp_username, smtp_settings.smtp_password)
                server.send_message(msg)
        else:
            # Use SSL (port 465 typically) or no encryption
            with smtplib.SMTP_SSL(smtp_settings.smtp_host, smtp_settings.smtp_port, timeout=10) as server:
                if smtp_settings.smtp_password:
                    server.login(smtp_settings.smtp_username, smtp_settings.smtp_password)
                server.send_message(msg)
        logger.info(f"Email sent successfully to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        raise


def create_welcome_email(username: str, password: str, login_url: str) -> tuple[str, str, str]:
    """Create welcome email content for new user.
    
    Args:
        username: Username of the new user
        password: Auto-generated password
        login_url: URL to login page
        
    Returns:
        Tuple of (subject, text_body, html_body)
    """
    subject = "Welcome to BamBuddy - Your Account Details"
    
    text_body = f"""Welcome to BamBuddy!

Your account has been created. Here are your login details:

Username: {username}
Password: {password}

You can login at: {login_url}

For security reasons, please change your password after your first login.

Best regards,
BamBuddy Team
"""
    
    html_body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 8px 8px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">Welcome to BamBuddy!</h1>
    </div>
    <div style="background: #f9f9f9; padding: 30px; border-radius: 0 0 8px 8px; border: 1px solid #ddd; border-top: none;">
        <p style="font-size: 16px;">Your account has been created. Here are your login details:</p>
        
        <div style="background: white; padding: 20px; border-radius: 4px; margin: 20px 0; border-left: 4px solid #667eea;">
            <p style="margin: 0 0 10px 0;"><strong>Username:</strong> <code style="background: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{username}</code></p>
            <p style="margin: 0;"><strong>Password:</strong> <code style="background: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{password}</code></p>
        </div>
        
        <div style="text-align: center; margin: 30px 0;">
            <a href="{login_url}" style="display: inline-block; background: #667eea; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">Login Now</a>
        </div>
        
        <p style="font-size: 14px; color: #666; border-top: 1px solid #ddd; padding-top: 20px; margin-top: 20px;">
            <strong>Security Note:</strong> For security reasons, please change your password after your first login.
        </p>
        
        <p style="font-size: 14px; color: #999; margin-top: 30px;">
            Best regards,<br>
            BamBuddy Team
        </p>
    </div>
</body>
</html>
"""
    
    return subject, text_body, html_body


def create_password_reset_email(username: str, password: str, login_url: str) -> tuple[str, str, str]:
    """Create password reset email content.
    
    Args:
        username: Username of the user
        password: New auto-generated password
        login_url: URL to login page
        
    Returns:
        Tuple of (subject, text_body, html_body)
    """
    subject = "BamBuddy - Your Password Has Been Reset"
    
    text_body = f"""Your BamBuddy password has been reset.

Your login details:

Username: {username}
New Password: {password}

You can login at: {login_url}

For security reasons, please change your password after logging in.

If you did not request this password reset, please contact your administrator immediately.

Best regards,
BamBuddy Team
"""
    
    html_body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 8px 8px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">Password Reset</h1>
    </div>
    <div style="background: #f9f9f9; padding: 30px; border-radius: 0 0 8px 8px; border: 1px solid #ddd; border-top: none;">
        <p style="font-size: 16px;">Your BamBuddy password has been reset.</p>
        
        <div style="background: white; padding: 20px; border-radius: 4px; margin: 20px 0; border-left: 4px solid #667eea;">
            <p style="margin: 0 0 10px 0;"><strong>Username:</strong> <code style="background: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{username}</code></p>
            <p style="margin: 0;"><strong>New Password:</strong> <code style="background: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{password}</code></p>
        </div>
        
        <div style="text-align: center; margin: 30px 0;">
            <a href="{login_url}" style="display: inline-block; background: #667eea; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; font-weight: bold;">Login Now</a>
        </div>
        
        <div style="background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; padding: 15px; margin: 20px 0;">
            <p style="margin: 0; font-size: 14px; color: #856404;">
                <strong>⚠️ Security Alert:</strong> If you did not request this password reset, please contact your administrator immediately.
            </p>
        </div>
        
        <p style="font-size: 14px; color: #666; border-top: 1px solid #ddd; padding-top: 20px; margin-top: 20px;">
            <strong>Security Note:</strong> For security reasons, please change your password after logging in.
        </p>
        
        <p style="font-size: 14px; color: #999; margin-top: 30px;">
            Best regards,<br>
            BamBuddy Team
        </p>
    </div>
</body>
</html>
"""
    
    return subject, text_body, html_body
