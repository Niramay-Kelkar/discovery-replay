-- Legacy bank demo: single member records table.
--
-- access_denied is a real column: it represents a genuine fact about a
-- member's record (an authorization business outcome), not an injected
-- test condition. The slow-load delay and the confirmation interstitial
-- are NOT modeled here -- they are app-layer conditions keyed off member
-- IDs in app.py, standing in for runtime behavior the replay layer must
-- cope with rather than properties of the data.

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
    access_denied   INTEGER NOT NULL DEFAULT 0
);
