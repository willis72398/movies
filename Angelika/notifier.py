"""
Email notification via Gmail SMTP.

Sends a multipart (plain text + HTML) email grouping new Angelika showtimes
by location, then by movie — one card per film per location.
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
# Helpers
# ---------------------------------------------------------------------------

def _group(showtimes: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Returns {location: {title: [showtime, ...]}} sorted chronologically."""
    by_loc: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for st in showtimes:
        by_loc[st.get("location", "Unknown")][st.get("title", "Unknown")].append(st)
    for loc in by_loc:
        for title in by_loc[loc]:
            by_loc[loc][title].sort(key=lambda s: s.get("show_datetime", ""))
    return by_loc


def _subject(by_loc: dict) -> str:
    total = sum(len(shows) for loc in by_loc.values() for shows in loc.values())
    all_titles = list({t for loc in by_loc.values() for t in loc})
    if len(all_titles) == 1:
        return f"[Angelika] {all_titles[0]} — {total} new showtime{'s' if total > 1 else ''}"
    if len(all_titles) <= 3:
        return f"[Angelika] {', '.join(all_titles)}"
    return f"[Angelika] {len(all_titles)} new films ({total} showtimes)"


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------

def _build_plain(by_loc: dict) -> str:
    total = sum(len(shows) for loc in by_loc.values() for shows in loc.values())
    lines = [f"Angelika bot detected {total} new showtime{'s' if total > 1 else ''}:\n"]
    for location, by_title in by_loc.items():
        n = sum(len(s) for s in by_title.values())
        lines.append("=" * 52)
        lines.append(f"  {location.upper()}  —  {n} new showing{'s' if n > 1 else ''}")
        lines.append("=" * 52)
        for title, shows in by_title.items():
            fmt = shows[0].get("format", "")
            lines.append(f"\n  {title}")
            if fmt:
                lines.append(f"  {fmt}")
            for st in shows:
                lines.append(f"  {st['show_date']}  {st['show_time']}  →  {st['ticket_url']}")
        lines.append("")
    lines += ["─" * 52, "You are receiving this because you set up the Angelika Bot."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_HTML_WRAPPER = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body     {{ font-family: Georgia, serif; background: #111; color: #eee;
              margin: 0; padding: 20px; }}
  .intro   {{ font-size: 13px; color: #aaa; font-family: Arial, sans-serif; margin-bottom: 20px; }}
  .intro strong {{ color: #fff; }}
  .card    {{ background: #1c1c1c; border: 1px solid #333; border-radius: 8px;
              padding: 18px 22px; margin-bottom: 16px; }}
  .location {{ font-size: 11px; letter-spacing: 2px; text-transform: uppercase;
               color: #f5c518; margin-bottom: 14px; border-bottom: 1px solid #333;
               padding-bottom: 8px; }}
  .title   {{ font-size: 20px; font-weight: bold; margin: 0 0 4px; color: #fff; }}
  .format  {{ font-size: 12px; color: #888; margin: 0 0 16px; }}
  .row     {{ display: flex; align-items: center; justify-content: space-between;
              padding: 8px 0; border-top: 1px solid #2a2a2a; }}
  .dt      {{ font-size: 14px; color: #ddd; font-family: Arial, sans-serif; }}
  .btn     {{ display: inline-block; background: #f5c518; color: #111;
              font-family: Arial, sans-serif; font-size: 12px; font-weight: bold;
              text-decoration: none; padding: 6px 14px; border-radius: 4px;
              white-space: nowrap; margin-left: 24px; }}
  .footer  {{ font-size: 11px; color: #555; margin-top: 24px; font-family: Arial, sans-serif; }}
  hr       {{ border: none; border-top: 1px solid #333; margin: 20px 0; }}
</style>
</head>
<body>
<p class="intro">🎬 Angelika bot detected
  <strong>{total} new showtime{plural}</strong>:</p>
{sections}
<p class="footer">You are receiving this because you set up the Angelika Bot.</p>
</body>
</html>"""

_CARD = """\
<div class="card">
  <p class="location">📍 {location}</p>
  <p class="title">{title}</p>
  <p class="format">{fmt}</p>
  {rows}
</div>"""

_ROW = """\
<div class="row">
  <span class="dt">{date} &nbsp;·&nbsp; {time}</span>
  <a class="btn" href="{url}">Buy Tickets</a>
</div>"""


def _build_html(by_loc: dict) -> str:
    total = sum(len(shows) for loc in by_loc.values() for shows in loc.values())
    section_parts = []
    for location, by_title in by_loc.items():
        cards = []
        for title, shows in by_title.items():
            fmt = shows[0].get("format", "")
            rows = "\n  ".join(
                _ROW.format(
                    date=escape(st["show_date"]),
                    time=escape(st["show_time"]),
                    url=escape(st["ticket_url"]),
                )
                for st in shows
            )
            cards.append(_CARD.format(
                location=escape(location),
                title=escape(title),
                fmt=escape(fmt) if fmt else '<span style="color:#555">—</span>',
                rows=rows,
            ))
        section_parts.append("\n".join(cards) + "\n<hr>")

    return _HTML_WRAPPER.format(
        total=total,
        plural="s" if total > 1 else "",
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
    if not new_showtimes:
        return

    by_loc = _group(new_showtimes)
    subject = _subject(by_loc)
    plain = _build_plain(by_loc)
    html = _build_html(by_loc)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = notify_email
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
            notify_email, len(new_showtimes), "s" if len(new_showtimes) > 1 else "",
        )
    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail authentication failed. Check GMAIL_APP_PASSWORD.")
    except Exception as exc:
        logger.error("Failed to send notification email: %s", exc)
