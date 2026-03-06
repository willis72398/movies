"""
Email notification via Gmail SMTP.

Uses Python's built-in smtplib — no third-party dependencies required.

To use Gmail SMTP you must generate an App Password:
  https://myaccount.google.com/apppasswords
(Regular Gmail passwords won't work when 2FA is enabled.)
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _format_showtime(showtime: dict) -> str:
    """Render a single showtime as a readable plain-text block."""
    movie = showtime.get("movieName") or showtime.get("movie", {}).get("name", "Unknown Movie")
    showtime_id = showtime.get("id", "N/A")
    dt = showtime.get("showDateTimeLocal") or showtime.get("dateTime", "Unknown time")
    attributes = showtime.get("attributes", [])
    attr_names = ", ".join(a.get("name", "") for a in attributes) if attributes else "IMAX"

    # Build a direct booking URL if possible.
    links = showtime.get("_links", {})
    book_link = links.get("purchase:showtime", {}).get("href", "")
    if not book_link:
        book_link = f"https://www.amctheatres.com/showtimes/{showtime_id}"

    lines = [
        f"  Movie   : {movie}",
        f"  Time    : {dt}",
        f"  Format  : {attr_names}",
        f"  Tickets : {book_link}",
        f"  ID      : {showtime_id}",
    ]
    return "\n".join(lines)


def send_notification(
    gmail_user: str,
    gmail_app_password: str,
    notify_email: str,
    new_showtimes: list[dict],
    theatre_name: str = "your AMC theatre",
) -> None:
    """
    Send a single email summarising all newly-detected IMAX showtimes.

    Does not raise on failure — logs the error instead so the polling loop
    can continue uninterrupted.
    """
    if not new_showtimes:
        return

    count = len(new_showtimes)
    subject = f"[AMC Bot] {count} new IMAX showing{'s' if count > 1 else ''} at {theatre_name}"

    body_lines = [
        f"The AMC IMAX bot detected {count} new showtime{'s' if count > 1 else ''} "
        f"at {theatre_name}:\n",
    ]
    for i, showtime in enumerate(new_showtimes, start=1):
        body_lines.append(f"--- Showing {i} ---")
        body_lines.append(_format_showtime(showtime))
        body_lines.append("")

    body_lines.append("---")
    body_lines.append("You are receiving this because you configured the AMC IMAX Polling Bot.")
    body = "\n".join(body_lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = notify_email
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_app_password)
            server.sendmail(gmail_user, notify_email, msg.as_string())
        logger.info(
            "Notification sent to %s (%d new showtime%s).",
            notify_email,
            count,
            "s" if count > 1 else "",
        )
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Gmail authentication failed. "
            "Make sure GMAIL_APP_PASSWORD is a valid App Password, not your account password."
        )
    except Exception as exc:
        logger.error("Failed to send notification email: %s", exc)
