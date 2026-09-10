"""Create and seed the SQLite database for the legacy bank demo.

Run directly:  python -m target_app.seed   (or)   python target_app/seed.py

Drops and recreates the members table every time so the demo target is
always in a known state.

Only genuine record facts live here. access_denied is a real column
(an authorization business outcome). The slow-load delay, the
confirmation interstitial and the account-maintenance hold are injected
in app.py by member ID, not stored -- M1003, M1006 and M1007 are
ordinary rows as far as the database is concerned.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "bank.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

# (member_id, first, last, dob, address, phone, email, balance_cents,
#  access_denied)
MEMBERS = [
    ("M1001", "Alice", "Nguyen", "1984-03-12",
     "412 Maple Street, Springfield, IL 62704",
     "(217) 555-0142", "alice.nguyen@example.com",
     1875042, 0),

    ("M1002", "Robert", "Delgado", "1971-11-02",
     "89 Birchwood Lane, Dayton, OH 45402",
     "(937) 555-0199", "robert.delgado@example.com",
     540388, 1),

    # Ordinary row. app.py injects a slow detail-page load for this ID.
    ("M1003", "Sandra", "Kim", "1990-07-25",
     "1200 Lakeshore Drive, Apt 5B, Chicago, IL 60611",
     "(312) 555-0177", "sandra.kim@example.com",
     9032150, 0),

    ("M1004", "James", "Okafor", "1965-01-30",
     "77 Cedar Court, Columbus, OH 43215",
     "(614) 555-0163", "james.okafor@example.com",
     221975, 0),

    ("M1005", "Maria", "Santos", "1988-09-14",
     "305 Willow Bend, Peoria, IL 61602",
     "(309) 555-0121", "maria.santos@example.com",
     4500000, 0),

    # Ordinary row. app.py injects a confirmation interstitial for this ID.
    ("M1006", "Pat", "Ashwood", "1979-05-08",
     "58 Junction Road, Akron, OH 44301",
     "(330) 555-0110", "pat.ashwood@example.com",
     1200000, 0),

    # Ordinary row. app.py injects an "account maintenance hold" screen for
    # this ID -- a state deliberately outside every declared expected
    # outcome, so replay has to escalate to a human rather than recognize it.
    ("M1007", "Dana", "Whitfield", "1993-02-19",
     "640 Ironwood Terrace, Toledo, OH 43604",
     "(419) 555-0188", "dana.whitfield@example.com",
     760514, 0),
]


def build(db_path=DB_PATH):
    with open(SCHEMA_PATH) as fh:
        schema = fh.read()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        conn.executemany(
            "INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            MEMBERS,
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


if __name__ == "__main__":
    path = build()
    print(f"Seeded {len(MEMBERS)} members into {path}")
