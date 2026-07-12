"""Minimal outbound email for password resets.

No mail provider is required to run this relay: by default the reset link is
written to the server log, which is sufficient for the single-user, local-first
deployment this project targets (the person resetting their own password is the
person who can read their own relay's logs).

Set RESEND_API_KEY to send a real email instead, via Resend's free tier
(https://resend.com — no credit card required for the free tier).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("contour.mailer")


def _log_fallback(to_email: str, reset_link: str) -> None:
    # This is the actual delivery mechanism when no mail provider is configured
    # (see module docstring), not just diagnostics -- print unconditionally so it
    # is visible regardless of how the caller's logging is configured.
    logger.warning("password reset requested for %s: %s", to_email, reset_link)
    print(f"[contour] password reset link for {to_email}: {reset_link}")


def send_reset_email(to_email: str, reset_link: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        _log_fallback(to_email, reset_link)
        return

    import httpx

    from_addr = os.environ.get("RESEND_FROM", "contour <onboarding@resend.dev>")
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": from_addr,
            "to": [to_email],
            "subject": "Reset your contour password",
            "html": (
                f"<p>Someone requested a password reset for this email.</p>"
                f'<p><a href="{reset_link}">Reset your password</a></p>'
                f"<p>This link expires in 30 minutes. If you didn't request this, "
                f"you can ignore this email.</p>"
            ),
        },
        timeout=10,
    )
    if resp.status_code >= 400:
        logger.warning(
            "Resend send failed (%s): %s -- falling back to logging reset link",
            resp.status_code, resp.text,
        )
        _log_fallback(to_email, reset_link)
