-- Legacy bank demo: single member records table.
-- Flags on individual rows drive the deliberately hostile behaviors
-- (access denied, slow load, unexpected interstitial).

DROP TABLE IF EXISTS members;

CREATE TABLE members (
    member_id       TEXT PRIMARY KEY,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    date_of_birth   TEXT NOT NULL,
    address         TEXT NOT NULL,
    phone           TEXT NOT NULL,
    email           TEXT NOT NULL,
    savings_balance INTEGER NOT NULL,   -- stored in cents
    access_denied   INTEGER NOT NULL DEFAULT 0,
    slow_load       INTEGER NOT NULL DEFAULT 0,
    interstitial    INTEGER NOT NULL DEFAULT 0
);
