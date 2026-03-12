CREATE TABLE IF NOT EXISTS angelika_showtimes (
    id           TEXT        PRIMARY KEY,
    title        TEXT        NOT NULL,
    show_date    TEXT        NOT NULL,
    show_time    TEXT        NOT NULL,
    show_datetime TEXT       NOT NULL,
    format       TEXT        NOT NULL DEFAULT '',
    location     TEXT        NOT NULL DEFAULT '',
    ticket_url   TEXT        NOT NULL,
    film_url     TEXT        NOT NULL,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hour         SMALLINT    NOT NULL,
    weekday      TEXT        NOT NULL
);
