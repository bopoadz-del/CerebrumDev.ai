"""Transactional email for account flows — stdlib smtplib, zero new deps.

When SMTP_HOST is unset the mailer reports ``sent=False`` and callers fall
back to honest dev-mode behavior (token surfaced in the API response).
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip())


def _deliver(msg: EmailMessage) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return False
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "")
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
    return True


def _sender() -> str:
    return (
        os.getenv("SMTP_FROM", "").strip()
        or os.getenv("SMTP_USER", "").strip()
        or "no-reply@cerebrum.dev"
    )


def send_verification_email(email: str, token: str) -> bool:
    """Send the verification link; returns False when SMTP is not configured."""
    if not smtp_configured():
        return False
    frontend = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    link = f"{frontend}/verify-email?token={token}"

    msg = EmailMessage()
    msg["Subject"] = "Verify your CerebrumDev.ai account"
    msg["From"] = _sender()
    msg["To"] = email
    msg.set_content(
        "Welcome to CerebrumDev.ai.\n\n"
        f"Verify your email address:\n{link}\n\n"
        "The link expires in 24 hours. If you did not register, ignore this email.\n"
    )
    return _deliver(msg)


def send_password_reset_email(email: str, token: str) -> bool:
    """Send the password-reset link; returns False when SMTP is not configured."""
    if not smtp_configured():
        return False
    frontend = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    link = f"{frontend}/reset-password?token={token}"

    msg = EmailMessage()
    msg["Subject"] = "Reset your CerebrumDev.ai password"
    msg["From"] = _sender()
    msg["To"] = email
    msg.set_content(
        "A password reset was requested for your CerebrumDev.ai account.\n\n"
        f"Set a new password:\n{link}\n\n"
        "The link expires in 1 hour and signs out all existing sessions. "
        "If you did not request this, ignore this email.\n"
    )
    return _deliver(msg)
