"""
Email notification via Gmail SMTP.

Sends a multipart email (plain text + HTML) listing all newly detected
Nitehawk special screenings, grouped by location.  Each showtime includes
a prominent "Buy Tickets" link so you can purchase with one click.

Gmail setup:
  1. Enable 2-Step Verification on your Google account.
  2. Create an App Password at https://myaccount.google.com/apppasswords
  3. Set GMAIL_APP_PASSWORD to that 16-character password (not your real password).
"""

import logging
import smtplib
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# ---------------------------------------------------------------------------
# Plain-text rendering
# ---------------------------------------------------------------------------


def _plain_showtime(st: dict) -> str:
    series = ", ".join(st.get("series", [])) or "—"
    purchase_url = st.get("purchase_url", "")
    details_url = st.get("details_url", "")
    lines = [
        f"  {st.get('title', 'Unknown')}",
        f"  {st.get('date', '?')}  @  {st.get('time', '?')}",
        f"  Series   : {series}",
        f"  BUY NOW  → {purchase_url}" if purchase_url else "  (no ticket link)",
        f"  Details  → {details_url}" if details_url else "",
    ]
    return "\n".join(l for l in lines if l)


def _build_plain(count: int, by_location: dict) -> str:
    lines = [
        f"The Nitehawk Special Screenings bot found {count} new "
        f"showtime{'s' if count > 1 else ''}:\n",
    ]
    for location, items in by_location.items():
        n = len(items)
        lines.append(f"{'=' * 52}")
        lines.append(f"  {location.upper()}  —  {n} new showing{'s' if n > 1 else ''}")
        lines.append(f"{'=' * 52}")

        by_title: dict[str, list] = defaultdict(list)
        for st in items:
            by_title[st.get("title", "Unknown")].append(st)

        for title, shows in by_title.items():
            series = ", ".join(shows[0].get("series", [])) or "—"
            lines.append(f"\n  {title}")
            lines.append(f"  {series}")
            for st in shows:
                url = st.get("purchase_url", "")
                lines.append(f"  {st.get('date','?')}  {st.get('time','?')}  →  {url}")
        lines.append("")
    lines.append("─" * 52)
    lines.append("You are receiving this because you set up the Nitehawk Special Screenings Bot.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_HTML_WRAPPER = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body      {{ font-family: Georgia, serif; background: #111; color: #eee;
               margin: 0; padding: 20px; }}
  .card     {{ background: #1c1c1c; border: 1px solid #333; border-radius: 8px;
               padding: 18px 22px; margin-bottom: 16px; }}
  .location {{ font-size: 11px; letter-spacing: 2px; text-transform: uppercase;
               color: #f5c518; margin-bottom: 14px; border-bottom: 1px solid #333;
               padding-bottom: 8px; }}
  .title    {{ font-size: 18px; font-weight: bold; margin: 0 0 4px; color: #fff; }}
  .datetime {{ font-size: 14px; color: #ccc; margin: 0 0 6px; }}
  .series   {{ font-size: 12px; color: #888; margin: 0 0 12px; }}
  .btn      {{ display: inline-block; background: #f5c518; color: #111;
               font-family: Arial, sans-serif; font-size: 13px; font-weight: bold;
               text-decoration: none; padding: 8px 18px; border-radius: 4px;
               margin-right: 8px; }}
  .btn-alt  {{ display: inline-block; background: transparent; color: #aaa;
               font-family: Arial, sans-serif; font-size: 12px;
               text-decoration: none; border: 1px solid #444;
               padding: 7px 14px; border-radius: 4px; }}
  .row      {{ display: flex; align-items: center; justify-content: space-between;
               padding: 8px 0; border-top: 1px solid #2a2a2a; }}
  .datetime {{ font-size: 14px; color: #ddd; font-family: Arial, sans-serif; }}
  .footer   {{ font-size: 11px; color: #555; margin-top: 24px; }}
  hr        {{ border: none; border-top: 1px solid #333; margin: 20px 0; }}
</style>
</head>
<body>
<p style="color:#aaa;font-size:13px;font-family:Arial,sans-serif;">
  🎬 Nitehawk Special Screenings Bot detected
  <strong style="color:#fff;">{count} new showtime{plural}</strong>:
</p>
{sections}
<p class="footer">
  You are receiving this because you set up the Nitehawk Special Screenings Bot.
</p>
</body>
</html>"""

_SECTION_HEADER = """\
<p class="location">📍 {location} &mdash; {n} new showing{plural}</p>"""

_CARD = """\
<div class="card">
  <p class="title">{title}</p>
  <p class="series">🎟 {series}</p>
  {rows}
</div>"""

_ROW = """\
<div class="row">
  <span class="datetime">🗓 {date} &nbsp;&nbsp;🕐 {time}</span>
  {buy_btn}
</div>"""

_BUY_BTN = """\
<a class="btn" href="{url}">Buy Tickets</a>
  <a class="btn-alt" href="{details}">Details</a>"""

_BUY_BTN_NO_DETAILS = """\
<a class="btn" href="{url}">Buy Tickets</a>"""


def _html_card(title: str, series: str, shows: list[dict]) -> str:
    rows = []
    for st in shows:
        purchase_url = st.get("purchase_url", "")
        details_url = st.get("details_url", "")
        if purchase_url and details_url:
            btn = _BUY_BTN.format(url=escape(purchase_url), details=escape(details_url))
        elif purchase_url:
            btn = _BUY_BTN_NO_DETAILS.format(url=escape(purchase_url))
        else:
            btn = '<span style="color:#888;font-size:12px;">No ticket link yet</span>'
        rows.append(_ROW.format(
            date=escape(st.get("date", "?")),
            time=escape(st.get("time", "?")),
            buy_btn=btn,
        ))
    return _CARD.format(
        title=escape(title),
        series=escape(series),
        rows="\n  ".join(rows),
    )


def _build_html(count: int, by_location: dict) -> str:
    section_parts = []
    for location, items in by_location.items():
        n = len(items)
        header = _SECTION_HEADER.format(
            location=escape(location),
            n=n,
            plural="s" if n > 1 else "",
        )
        # Group by title within the location
        by_title: dict[str, list] = defaultdict(list)
        for st in items:
            by_title[st.get("title", "Unknown")].append(st)

        cards = "\n".join(
            _html_card(
                title,
                ", ".join(shows[0].get("series", [])) or "—",
                shows,
            )
            for title, shows in by_title.items()
        )
        section_parts.append(f"{header}\n{cards}\n<hr>")

    return _HTML_WRAPPER.format(
        count=count,
        plural="s" if count > 1 else "",
        sections="\n".join(section_parts),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_notification(
    gmail_user: str,
    gmail_app_password: str,
    notify_email: str,
    new_showtimes: list[dict],
) -> None:
    """
    Send a multipart (plain text + HTML) email summarising all newly-detected
    special screenings.  The HTML version includes a clickable "Buy Tickets"
    button for each showtime.

    Does not raise on failure — errors are logged so polling can continue.
    """
    if not new_showtimes:
        return

    by_location: dict[str, list[dict]] = defaultdict(list)
    for st in new_showtimes:
        by_location[st.get("location", "Unknown")].append(st)

    count = len(new_showtimes)
    subject = (
        f"[Nitehawk Bot] {count} new special screening"
        f"{'s' if count > 1 else ''} detected"
    )

    plain = _build_plain(count, by_location)
    html = _build_html(count, by_location)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = notify_email
    # Plain text first, HTML second — email clients prefer the last part they support
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

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
            "Ensure GMAIL_APP_PASSWORD is a valid App Password — "
            "see https://myaccount.google.com/apppasswords"
        )
    except Exception as exc:
        logger.error("Failed to send notification email: %s", exc)
