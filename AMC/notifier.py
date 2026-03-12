"""
Email notification via Gmail SMTP.

Sends a multipart (plain text + HTML) email grouping new IMAX showtimes
by movie — one card per film, with each showtime listed as a row inside
that card.
"""

import logging
import smtplib
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _book_url(st: dict) -> str:
    links = st.get("_links", {})
    url = links.get("purchase:showtime", {}).get("href", "")
    return url or f"https://www.amctheatres.com/showtimes/{st.get('id', '')}"


def _format_dt(iso: str) -> str:
    """'2026-03-12T22:30:00' → 'Thu Mar 12 · 10:30pm'"""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%a %b %-d · %-I:%M%p").lower().replace("am", "am").replace("pm", "pm")
    except ValueError:
        return iso


def _group_by_movie(showtimes: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for st in showtimes:
        grouped[st.get("movieName", "Unknown")].append(st)
    # Sort each movie's showtimes chronologically
    for key in grouped:
        grouped[key].sort(key=lambda s: s.get("showDateTimeLocal", ""))
    return grouped


def _subject(grouped: dict) -> str:
    movies = list(grouped.keys())
    total_shows = sum(len(v) for v in grouped.values())
    if len(movies) == 1:
        n = total_shows
        return f"[AMC IMAX] {movies[0]} — {n} new showtime{'s' if n > 1 else ''}"
    if len(movies) <= 3:
        return f"[AMC IMAX] {', '.join(movies)}"
    return f"[AMC IMAX] {len(movies)} new films ({total_shows} showtimes)"


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------

def _build_plain(grouped: dict, theatre_name: str) -> str:
    total = sum(len(v) for v in grouped.values())
    lines = [
        f"AMC IMAX bot detected {total} new showtime{'s' if total > 1 else ''} "
        f"at {theatre_name}:\n"
    ]
    for movie, shows in grouped.items():
        attrs = shows[0].get("attributes", [])
        fmt = ", ".join(a.get("name", "") for a in attrs) if attrs else "IMAX"
        lines.append(f"{'─' * 48}")
        lines.append(f"  {movie}")
        lines.append(f"  {fmt}\n")
        for st in shows:
            lines.append(f"  {_format_dt(st.get('showDateTimeLocal', ''))}  →  {_book_url(st)}")
        lines.append("")
    lines += ["─" * 48, "You are receiving this because you set up the AMC IMAX Bot."]
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
  .theatre {{ font-size: 11px; letter-spacing: 2px; text-transform: uppercase;
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
              white-space: nowrap; }}
  .footer  {{ font-size: 11px; color: #555; margin-top: 24px; font-family: Arial, sans-serif; }}
</style>
</head>
<body>
<p class="intro">🎬 AMC IMAX bot detected
  <strong>{total} new showtime{plural}</strong> at {theatre}:</p>
{cards}
<p class="footer">You are receiving this because you set up the AMC IMAX Bot.</p>
</body>
</html>"""

_CARD = """\
<div class="card">
  <p class="theatre">🎥 {theatre}</p>
  <p class="title">{title}</p>
  <p class="format">{fmt}</p>
  {rows}
</div>"""

_ROW = """\
<div class="row">
  <span class="dt">{dt}</span>
  <a class="btn" href="{url}">Buy Tickets</a>
</div>"""


def _build_html(grouped: dict, theatre_name: str) -> str:
    total = sum(len(v) for v in grouped.values())
    cards = []
    for movie, shows in grouped.items():
        attrs = shows[0].get("attributes", [])
        fmt = ", ".join(a.get("name", "") for a in attrs) if attrs else "IMAX"
        rows = "\n  ".join(
            _ROW.format(dt=escape(_format_dt(st.get("showDateTimeLocal", ""))),
                        url=escape(_book_url(st)))
            for st in shows
        )
        cards.append(_CARD.format(
            theatre=escape(theatre_name),
            title=escape(movie),
            fmt=escape(fmt),
            rows=rows,
        ))
    return _HTML_WRAPPER.format(
        total=total,
        plural="s" if total > 1 else "",
        theatre=escape(theatre_name),
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
    theatre_name: str = "AMC Lincoln Square 13",
) -> None:
    if not new_showtimes:
        return

    grouped = _group_by_movie(new_showtimes)
    subject = _subject(grouped)
    plain = _build_plain(grouped, theatre_name)
    html = _build_html(grouped, theatre_name)

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
