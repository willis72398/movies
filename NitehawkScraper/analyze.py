"""
Nitehawk discovery-log analyser.

Reads the discoveries table from nitehawk.db and prints a breakdown of
when Nitehawk tends to post new programming.  Use this after a few weeks
of data to tune PEAK_HOURS_START / PEAK_HOURS_END.

Usage
-----
    python analyze.py                   # reads ./nitehawk.db
    python analyze.py path/to/nitehawk.db

Useful ad-hoc queries (run directly with sqlite3 CLI):
    sqlite3 nitehawk.db "SELECT * FROM discoveries ORDER BY discovered_at DESC LIMIT 20;"
    sqlite3 nitehawk.db "SELECT title, date, showtime, location FROM discoveries;"
    sqlite3 nitehawk.db "SELECT hour, COUNT(*) FROM discoveries GROUP BY hour ORDER BY hour;"
"""

import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BAR_WIDTH = 40
WEEKDAY_ORDER = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _bar(count: int, max_count: int) -> str:
    filled = round(count / max_count * BAR_WIDTH) if max_count else 0
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _pct(count: int, total: int) -> str:
    return f"{count / total * 100:.1f}%" if total else "—"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("nitehawk.db")

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("The bot writes this file automatically. Run for a few weeks, then try again.")
        sys.exit(0)

    conn = _connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0]

    if total == 0:
        print("No discoveries recorded yet — no data to analyse.")
        sys.exit(0)

    print(f"\nNitehawk discovery database: {db_path}")
    print(f"Total discoveries recorded : {total}")
    print(f"Date range                 : ", end="")
    row = conn.execute(
        "SELECT MIN(discovered_at), MAX(discovered_at) FROM discoveries"
    ).fetchone()
    print(f"{row[0]}  →  {row[1]}\n")

    # ------------------------------------------------------------------
    # 1. Hour-of-day (the key chart for tuning peak hours)
    # ------------------------------------------------------------------
    hour_rows = conn.execute(
        "SELECT hour, COUNT(*) as cnt FROM discoveries GROUP BY hour ORDER BY hour"
    ).fetchall()
    hour_map = {r["hour"]: r["cnt"] for r in hour_rows}
    max_h = max(hour_map.values(), default=1)

    print("─" * 62)
    print("  DISCOVERIES BY HOUR OF DAY (local time)")
    print("  Use this to set PEAK_HOURS_START and PEAK_HOURS_END")
    print("─" * 62)
    for hour in range(24):
        count = hour_map.get(hour, 0)
        label = f"{hour:02d}:00–{hour:02d}:59"
        bar = _bar(count, max_h)
        print(f"  {label}  {bar}  {count:3d}  ({_pct(count, total)})")

    # Auto-suggest: hours that together cover ≥70% of discoveries
    sorted_hours = sorted(hour_map, key=lambda h: -hour_map[h])
    cumulative, peak_hours = 0, []
    for h in sorted_hours:
        peak_hours.append(h)
        cumulative += hour_map[h]
        if cumulative / total >= 0.70:
            break
    if peak_hours:
        ph = sorted(peak_hours)
        print(
            f"\n  → Suggested peak window (covers ~70% of discoveries): "
            f"{ph[0]:02d}:00–{(ph[-1] + 1):02d}:00"
        )
        print(
            f"    Set PEAK_HOURS_START={ph[0]:02d}:00  "
            f"PEAK_HOURS_END={(ph[-1] + 1):02d}:00  in your .env"
        )

    # ------------------------------------------------------------------
    # 2. Day-of-week
    # ------------------------------------------------------------------
    day_rows = conn.execute(
        "SELECT weekday, COUNT(*) as cnt FROM discoveries GROUP BY weekday"
    ).fetchall()
    day_map = {r["weekday"]: r["cnt"] for r in day_rows}
    max_d = max((day_map.get(d, 0) for d in WEEKDAY_ORDER), default=1)

    print("\n" + "─" * 62)
    print("  DISCOVERIES BY DAY OF WEEK")
    print("─" * 62)
    for day in WEEKDAY_ORDER:
        count = day_map.get(day, 0)
        print(f"  {day:<10}  {_bar(count, max_d)}  {count:3d}  ({_pct(count, total)})")

    # ------------------------------------------------------------------
    # 3. Location breakdown
    # ------------------------------------------------------------------
    loc_rows = conn.execute(
        "SELECT location, COUNT(*) as cnt FROM discoveries "
        "GROUP BY location ORDER BY cnt DESC"
    ).fetchall()

    print("\n" + "─" * 62)
    print("  DISCOVERIES BY LOCATION")
    print("─" * 62)
    for row in loc_rows:
        print(f"  {row['location']:<22}  {row['cnt']:3d}  ({_pct(row['cnt'], total)})")

    # ------------------------------------------------------------------
    # 4. Top series (pipe-separated field → parse in Python)
    # ------------------------------------------------------------------
    series_rows = conn.execute("SELECT series FROM discoveries").fetchall()
    series_counts: dict[str, int] = {}
    for row in series_rows:
        for label in row["series"].split("|"):
            label = label.strip()
            if label:
                series_counts[label] = series_counts.get(label, 0) + 1

    if series_counts:
        print("\n" + "─" * 62)
        print("  TOP SERIES  (by number of new showtimes detected)")
        print("─" * 62)
        for series, count in sorted(series_counts.items(), key=lambda x: -x[1])[:12]:
            print(f"  {series:<38}  {count:3d}")

    # ------------------------------------------------------------------
    # 5. Most recently discovered showtimes
    # ------------------------------------------------------------------
    recent = conn.execute(
        """
        SELECT discovered_at, title, date, showtime, location
        FROM discoveries
        ORDER BY discovered_at DESC
        LIMIT 10
        """
    ).fetchall()

    print("\n" + "─" * 62)
    print("  10 MOST RECENTLY DISCOVERED SHOWTIMES")
    print("─" * 62)
    for row in recent:
        print(
            f"  {row['discovered_at']}  "
            f"{row['title'][:28]:<28}  "
            f"{row['date']:<14}  {row['showtime']}"
        )

    print()
    conn.close()


if __name__ == "__main__":
    main()
