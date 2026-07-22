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


def send_verification_email(email: str, token: str) -> bool:
    """Send the verification link; returns False when SMTP is not configured."""
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return False
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "")
    sender = os.getenv("SMTP_FROM", "").strip() or user or "no-reply@cerebrum.dev"
    frontend = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    link = f"{frontend}/verify-email?token={token}"

    msg = EmailMessage()
    msg["Subject"] = "Verify your CerebrumDev.ai account"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        "Welcome to CerebrumDev.ai.\n\n"
        f"Verify your email address:\n{link}\n\n"
        "The link expires in 24 hours. If you did not register, ignore this email.\n"
    )
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
    return True
