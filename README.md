# Movies

A collection of NYC cinema polling bots running on a Raspberry Pi 5. Each bot monitors a theatre's website for new showtimes and sends a Gmail notification when previously-unseen screenings appear. All bots share a single PostgreSQL database, with `first_seen`, `hour`, and `weekday` recorded per discovery for future peak-hours analysis.

## Bots

| Bot | Theatre | Method | Poll Interval |
|---|---|---|---|
| **AMC** | AMC Lincoln Square 13 — IMAX only | Playwright (headless Chromium) + BeautifulSoup | 15 min |
| **Nitehawk** | Nitehawk Cinema (Williamsburg + Prospect Park) — Special Screenings only | requests + BeautifulSoup | 30 min |
| **Metrograph** | Metrograph NYC | requests + BeautifulSoup | 30 min |

## Architecture

```
Movies/
├── AMC/                   # AMC Lincoln Square 13 IMAX scraper
│   ├── amc_client.py      # Playwright session + BeautifulSoup parser
│   ├── main.py            # APScheduler polling loop
│   ├── notifier.py        # Gmail SMTP notifications
│   ├── state.py           # PostgreSQL state (seen IDs, discovery log)
│   ├── Dockerfile
│   └── requirements.txt
├── Nitehawk/              # Nitehawk Cinema special screenings scraper
│   ├── scraper.py         # BeautifulSoup parser
│   ├── main.py
│   ├── notifier.py
│   ├── state.py
│   ├── analyze.py         # Peak-hour analysis tool
│   ├── Dockerfile
│   └── requirements.txt
├── Metrograph/            # Metrograph NYC scraper
│   ├── scraper.py         # BeautifulSoup parser
│   ├── main.py
│   ├── notifier.py
│   ├── state.py
│   ├── Dockerfile
│   └── requirements.txt
├── migrations/
│   ├── 001_init.sql       # amc_showtimes + nitehawk_showtimes tables
│   └── 002_metrograph.sql # metrograph_showtimes table
├── docker-compose.yml     # postgres + all three bots
└── .env                   # secrets (gitignored)
```

Each bot is an independent Docker container. They share a single `postgres` service and a named volume (`postgres-data`) that persists across restarts and rebuilds.

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/willis72398/movies.git ~/movies
cd ~/movies
cp .env.example .env
# Fill in your credentials in .env
```

### 2. `.env` variables

```env
# PostgreSQL
POSTGRES_PASSWORD=your_password

# Gmail SMTP (shared by all bots)
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # 16-char App Password
NOTIFY_EMAIL=you@gmail.com

# Poll intervals (minutes)
AMC_POLL_INTERVAL_MINUTES=15
METROGRAPH_POLL_INTERVAL_MINUTES=30
NITEHAWK_POLL_INTERVAL_MINUTES=30
NITEHAWK_PEAK_POLL_INTERVAL_MINUTES=15
NITEHAWK_PEAK_HOURS_START=11:00
NITEHAWK_PEAK_HOURS_END=18:00
```

Gmail requires an [App Password](https://myaccount.google.com/apppasswords) — your regular password won't work with 2FA enabled.

### 3. Build and start

```bash
docker compose build
docker compose up -d
```

The AMC build will take a few minutes the first time — it downloads Playwright's Chromium (~100MB). Subsequent builds use the cached layer.

### 4. Check status

```bash
docker compose ps
docker compose logs amc --tail=20
docker compose logs nitehawk --tail=20
docker compose logs metrograph --tail=20
```

## Deployment

Running on a Raspberry Pi 5 (`linux/arm64`) under Docker Compose with `restart: unless-stopped`. Managed via [Portainer](https://portainer.willisschubert.com).

To update after a code change:

```bash
git pull
docker compose build <service>
docker compose up -d <service>
```

## Database

All discoveries are stored in PostgreSQL with `first_seen`, `hour`, and `weekday` columns. This data will be used to tune polling intervals once enough history has accumulated — the goal is to poll more frequently during windows when new showtimes are typically announced.

Connect locally: `psql postgresql://movies:<password>@localhost:5432/movies`
