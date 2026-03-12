-- Metrograph NYC bot
-- Stores every showtime the bot has ever detected.
-- `id` is the Vista ticketing session ID (txtSessionId) from the ticket URL.
CREATE TABLE IF NOT EXISTS metrograph_showtimes (
    id            TEXT        PRIMARY KEY,
    title         TEXT        NOT NULL,
    show_date     TEXT        NOT NULL,       -- "2026-03-12"
    show_time     TEXT        NOT NULL,       -- "4:00pm"
    show_datetime TEXT        NOT NULL,       -- ISO-8601, e.g. "2026-03-12T16:00:00"
    director      TEXT        NOT NULL DEFAULT '',
    year          TEXT        NOT NULL DEFAULT '',
    runtime       TEXT        NOT NULL DEFAULT '',
    format        TEXT        NOT NULL DEFAULT '',
    film_url      TEXT        NOT NULL,
    ticket_url    TEXT        NOT NULL,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hour          SMALLINT    NOT NULL,       -- 0–23, for peak-hour analysis
    weekday       TEXT        NOT NULL        -- "Monday" … "Sunday"
);
