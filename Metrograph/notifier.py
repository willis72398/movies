"""
Email notification via Gmail SMTP.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _format_showtime(st: dict) -> str:
    meta = " / ".join(filter(None, [st.get("director"), st.get("year"), st.get("format")]))
    lines = [
        f"  Movie   : {st['title']}",
        f"  Date    : {st['show_date']}",
        f"  Time    : {st['show_time']}",
    ]
    if meta:
        lines.append(f"  Info    : {meta}")
    lines.append(f"  Tickets : {st['ticket_url']}")
    return "\n".join(lines)


def send_notification(
    gmail_user: str,
    gmail_app_password: str,
    notify_email: str,
    new_showtimes: list[dict],
) -> None:
    if not new_showtimes:
        return

    count = len(new_showtimes)
    subject = f"[Metrograph Bot] {count} new showtime{'s' if count > 1 else ''}"

    body_lines = [
        f"The Metrograph bot detected {count} new showtime{'s' if count > 1 else ''}:\n"
    ]
    for i, st in enumerate(new_showtimes, start=1):
        body_lines.append(f"--- Showing {i} ---")
        body_lines.append(_format_showtime(st))
        body_lines.append("")

    body_lines.append("---")
    body_lines.append("You are receiving this because you configured the Metrograph Polling Bot.")
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
        logger.info("Notification sent to %s (%d new showtime%s).", notify_email, count, "s" if count > 1 else "")
    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail authentication failed. Check GMAIL_APP_PASSWORD.")
    except Exception as exc:
        logger.error("Failed to send notification email: %s", exc)
