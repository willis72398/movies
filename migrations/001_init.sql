-- Movies monorepo — initial schema
-- Run automatically by postgres Docker image on first startup
-- (any .sql file in /docker-entrypoint-initdb.d/ is executed once against a fresh DB)

-- ---------------------------------------------------------------------------
-- Nitehawk Cinema bot
-- Stores every special screening showtime the bot has ever detected.
-- `id` is the Filmbot purchase/showtime ID (unique per individual screening).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nitehawk_showtimes (
    id           TEXT        PRIMARY KEY,
    title        TEXT        NOT NULL,
    date         TEXT        NOT NULL,       -- human-readable, e.g. "Wed, Apr 15"
    showtime     TEXT        NOT NULL,       -- e.g. "7:15 pm"
    location     TEXT        NOT NULL,       -- "Williamsburg" | "Prospect Park"
    series       TEXT        NOT NULL,       -- pipe-separated series labels
    purchase_url TEXT        NOT NULL,
    details_url  TEXT        NOT NULL,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hour         SMALLINT    NOT NULL,       -- 0–23, for peak-hour analysis
    weekday      TEXT        NOT NULL        -- "Monday" … "Sunday"
);

-- ---------------------------------------------------------------------------
-- AMC IMAX bot
-- Stores every IMAX showtime the bot has ever detected.
-- `id` is the AMC showtime ID from the API.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS amc_showtimes (
    id            TEXT        PRIMARY KEY,
    movie_name    TEXT        NOT NULL,
    theatre_name  TEXT        NOT NULL,
    show_datetime TEXT        NOT NULL,      -- ISO-8601 string from AMC API
    format        TEXT        NOT NULL,      -- e.g. "IMAX", "IMAX 3D"
    book_url      TEXT        NOT NULL,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hour          SMALLINT    NOT NULL,
    weekday       TEXT        NOT NULL
);
