"""Legacy bank demo app.

A deliberately hostile automation target: nested table layouts, an
unrelated iframe, and non-semantic CSS class names. Interactive elements
and data cells still carry correct ARIA roles / accessible names so the
accessibility tree stays usable (see target_app/README.md).

Run:
    python target_app/seed.py          # once, to build bank.db
    flask --app target_app.app run     # or: python target_app/app.py
"""
import os
import sqlite3
import time

from flask import Flask, g, render_template, request

DB_PATH = os.path.join(os.path.dirname(__file__), "bank.db")
SLOW_LOAD_SECONDS = 4

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def format_balance(cents):
    return "${:,.2f}".format(cents / 100)


@app.route("/")
def index():
    return render_template("search.html")


@app.route("/search")
def search():
    field = request.args.get("field", "member_id")
    query = (request.args.get("q") or "").strip()

    if field not in ("member_id", "last_name"):
        field = "member_id"

    if not query:
        return render_template("search.html", error="Enter a search term.")

    db = get_db()
    if field == "member_id":
        rows = db.execute(
            "SELECT * FROM members WHERE member_id = ? COLLATE NOCASE",
            (query,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM members WHERE last_name = ? COLLATE NOCASE",
            (query,),
        ).fetchall()

    results = [
        {
            "member_id": r["member_id"],
            "name": f"{r['first_name']} {r['last_name']}",
            # Address is "street, City, ST ZIP" (street itself may contain
            # commas), so the city is always the second-to-last segment.
            "city": (r["address"].split(",")[-2].strip()
                     if r["address"].count(",") >= 2 else ""),
        }
        for r in rows
    ]
    return render_template(
        "results.html", results=results, query=query, field=field
    )


@app.route("/member/<member_id>")
def member_detail(member_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM members WHERE member_id = ? COLLATE NOCASE",
        (member_id,),
    ).fetchone()

    if row is None:
        return render_template("not_found.html", query=member_id,
                               field="member_id"), 404

    if row["access_denied"]:
        return render_template("access_denied.html", member_id=row["member_id"]), 403

    if row["interstitial"] and request.args.get("confirm") != "yes":
        return render_template("interstitial.html", member_id=row["member_id"])

    if row["slow_load"]:
        time.sleep(SLOW_LOAD_SECONDS)

    member = dict(row)
    member["full_name"] = f"{row['first_name']} {row['last_name']}"
    member["savings_balance_display"] = format_balance(row["savings_balance"])
    return render_template("detail.html", m=member)


if __name__ == "__main__":
    app.run(debug=False, port=5000)
