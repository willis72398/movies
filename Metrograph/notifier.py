"""
Email notification via Gmail SMTP.

Sends a multipart (plain text + HTML) email grouping new showtimes by
movie — one card per film with metadata, then a row per showtime.
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

def _group_by_title(showtimes: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for st in showtimes:
        grouped[st["title"]].append(st)
    for key in grouped:
        grouped[key].sort(key=lambda s: s.get("show_datetime", ""))
    return grouped


def _meta(st: dict) -> str:
    return " · ".join(filter(None, [st.get("director"), st.get("year"), st.get("format")]))


def _subject(grouped: dict) -> str:
    movies = list(grouped.keys())
    total = sum(len(v) for v in grouped.values())
    if len(movies) == 1:
        n = total
        return f"[Metrograph] {movies[0]} — {n} new showtime{'s' if n > 1 else ''}"
    if len(movies) <= 3:
        return f"[Metrograph] {', '.join(movies)}"
    return f"[Metrograph] {len(movies)} new films ({total} showtimes)"


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------

def _build_plain(grouped: dict) -> str:
    total = sum(len(v) for v in grouped.values())
    lines = [f"Metrograph bot detected {total} new showtime{'s' if total > 1 else ''}:\n"]
    for title, shows in grouped.items():
        lines.append("─" * 48)
        lines.append(f"  {title}")
        meta = _meta(shows[0])
        if meta:
            lines.append(f"  {meta}")
        lines.append("")
        for st in shows:
            lines.append(f"  {st['show_date']}  {st['show_time']}  →  {st['ticket_url']}")
        lines.append("")
    lines += ["─" * 48, "You are receiving this because you set up the Metrograph Bot."]
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
  .venue   {{ font-size: 11px; letter-spacing: 2px; text-transform: uppercase;
              color: #f5c518; margin-bottom: 14px; border-bottom: 1px solid #333;
              padding-bottom: 8px; }}
  .title   {{ font-size: 20px; font-weight: bold; margin: 0 0 4px; color: #fff; }}
  .meta    {{ font-size: 12px; color: #888; margin: 0 0 16px; }}
  .row     {{ display: flex; align-items: center; justify-content: space-between;
              padding: 8px 0; border-top: 1px solid #2a2a2a; }}
  .dt      {{ font-size: 14px; color: #ddd; font-family: Arial, sans-serif; }}
  .btn     {{ display: inline-block; background: #f5c518; color: #111;
              font-family: Arial, sans-serif; font-size: 12px; font-weight: bold;
              text-decoration: none; padding: 6px 14px; border-radius: 4px;
              white-space: nowrap; }}
  .film-link {{ font-size: 11px; color: #666; font-family: Arial, sans-serif;
                text-decoration: none; margin-left: 10px; }}
  .footer  {{ font-size: 11px; color: #555; margin-top: 24px; font-family: Arial, sans-serif; }}
</style>
</head>
<body>
<p class="intro">🎬 Metrograph bot detected
  <strong>{total} new showtime{plural}</strong>:</p>
{cards}
<p class="footer">You are receiving this because you set up the Metrograph Bot.</p>
</body>
</html>"""

_CARD = """\
<div class="card">
  <p class="venue">📍 Metrograph NYC</p>
  <p class="title">{title}</p>
  <p class="meta">{meta}</p>
  {rows}
</div>"""

_ROW = """\
<div class="row">
  <span class="dt">{date} &nbsp;·&nbsp; {time}</span>
  <a class="btn" href="{url}">Buy Tickets</a>
</div>"""


def _build_html(grouped: dict) -> str:
    total = sum(len(v) for v in grouped.values())
    cards = []
    for title, shows in grouped.items():
        meta = _meta(shows[0])
        rows = "\n  ".join(
            _ROW.format(
                date=escape(st["show_date"]),
                time=escape(st["show_time"]),
                url=escape(st["ticket_url"]),
            )
            for st in shows
        )
        cards.append(_CARD.format(
            title=escape(title),
            meta=escape(meta) if meta else '<span style="color:#555">—</span>',
            rows=rows,
        ))
    return _HTML_WRAPPER.format(
        total=total,
        plural="s" if total > 1 else "",
        cards="\n".join(cards),
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

    grouped = _group_by_title(new_showtimes)
    subject = _subject(grouped)
    plain = _build_plain(grouped)
    html = _build_html(grouped)

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
        logger.info("Notification sent to %s (%d new showtime%s).",
                    notify_email, len(new_showtimes), "s" if len(new_showtimes) > 1 else "")
    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail authentication failed. Check GMAIL_APP_PASSWORD.")
    except Exception as exc:
        logger.error("Failed to send notification email: %s", exc)
