"""Operator console for human-in-the-loop escalation (Section 3.6).

Intentionally minimal. Section 3.6 puts a full co-browsing console out of
scope -- the operator is assumed to be sitting at the machine and can
drive the replay browser window directly. What this app provides is the
part that has to be real: visibility into *why* replay is blocked, and a
control that hands control back.

- ``GET  /``                      -- pending escalations + recent history,
                                     each with goal / step / trigger /
                                     expected vs. observed / screenshot.
- ``GET  /screenshot/<run_id>/<name>`` -- serve a run's escalation
                                     screenshot, scoped to that run_id.
- ``POST /resume/<id>``           -- mark the escalation resumed, which
                                     unblocks the polling replay process.
                                     The operator confirms they have taken
                                     control of the live browser and, if
                                     needed, completed the blocked step by
                                     hand; replay then re-enters
                                     SETTLE/CHECK for that step only and
                                     never re-runs its ACT.

Run:
    python -m agent.operator_console --port 5002
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from flask import (
    Flask,
    abort,
    redirect,
    render_template_string,
    request,
    send_file,
    url_for,
)

from agent.escalation import DEFAULT_DB_PATH, SessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def create_app(
    db_path: str = DEFAULT_DB_PATH,
    evidence_replays_dir: str | Path = REPO_ROOT / "evidence" / "replays",
) -> Flask:
    app = Flask(__name__)
    store = SessionStore(db_path)
    replays_root = Path(evidence_replays_dir).resolve()

    @app.get("/")
    def index():
        return render_template_string(
            _INDEX_HTML,
            pending=store.list_pending(),
            history=[r for r in store.list_all(50) if r["status"] != "pending"],
        )

    @app.get("/screenshot/<run_id>/<path:name>")
    def screenshot(run_id: str, name: str):
        # scope 1: there must be an escalation for THIS run_id whose
        # recorded screenshot is the file being asked for.
        recs = [r for r in store.list_for_run(run_id) if r.get("screenshot_path")]
        if not recs:
            abort(404)
        # scope 2: the resolved path must sit inside this run's own
        # evidence directory -- no traversal, no other run's files.
        run_dir = (replays_root / run_id).resolve()
        target = (run_dir / "screenshots" / os.path.basename(name)).resolve()
        if run_dir not in target.parents:
            abort(404)
        if not any(Path(r["screenshot_path"]).resolve() == target for r in recs):
            abort(404)
        if not target.exists():
            abort(404)
        return send_file(target, mimetype="image/png")

    @app.post("/resume/<int:esc_id>")
    def resume(esc_id: int):
        rec = store.get(esc_id)
        if rec is None:
            abort(404)
        operator = (request.form.get("operator") or "operator").strip() or "operator"
        note = (request.form.get("note") or "").strip() or None
        ok = store.mark_resumed(esc_id, resumed_by=operator, operator_note=note)
        if not ok:
            # already resumed / timed out - not an error worth a 500
            return redirect(url_for("index"))
        return redirect(url_for("index"))

    return app


_INDEX_HTML = """
<!doctype html>
<title>Replay operator console</title>
<style>
  body { font-family: -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #1a1a1a; }
  h1 { font-size: 1.3rem; }
  .esc { border: 1px solid #c14545; border-radius: 6px; padding: 1rem; margin: 1rem 0; background: #fff6f6; }
  .esc h2 { font-size: 1.05rem; margin: 0 0 .5rem; }
  .done { border-color: #bbb; background: #f6f6f6; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: .2rem .8rem; margin: .5rem 0; }
  dt { font-weight: 600; color: #555; }
  code { background: #eee; padding: 0 .3rem; border-radius: 3px; }
  img { max-width: 760px; border: 1px solid #999; display: block; margin: .6rem 0; }
  form.resume { margin-top: .6rem; }
  input[type=text] { padding: .3rem; }
  button { padding: .4rem .9rem; font-weight: 600; cursor: pointer; }
  .muted { color: #777; font-size: .85rem; }
</style>
<h1>Replay operator console</h1>
<p class="muted">
  A blocked replay process is polling the session store. Take control of
  the live browser window, do whatever the step needs, then Resume &mdash;
  replay re-runs SETTLE/CHECK for that step only (never ACT).
</p>

<h2>Pending ({{ pending|length }})</h2>
{% if not pending %}<p class="muted">Nothing waiting.</p>{% endif %}
{% for e in pending %}
  <div class="esc">
    <h2>#{{ e.id }} &middot; {{ e.capability_id }} &middot; step
        {{ e.step_ordinal }} (<code>{{ e.step_id }}</code>)</h2>
    <dl>
      <dt>run</dt><dd><code>{{ e.run_id }}</code></dd>
      <dt>goal</dt><dd>{{ e.goal or "—" }}</dd>
      <dt>trigger</dt><dd><code>{{ e.trigger }}</code> ({{ e.phase }} phase)</dd>
      <dt>expected</dt><dd>{{ e.expected or "—" }}</dd>
      <dt>observed</dt><dd>{{ e.observed or "—" }}</dd>
      <dt>opened</dt><dd>{{ e.created_at }}</dd>
      <dt>deadline</dt><dd>{{ e.handoff_deadline_at or "—" }}</dd>
    </dl>
    {% if e.screenshot_path %}
      <img src="{{ url_for('screenshot', run_id=e.run_id,
                           name=e.screenshot_path.split('/')[-1]) }}"
           alt="page at the point replay blocked">
    {% endif %}
    <form class="resume" method="post" action="{{ url_for('resume', esc_id=e.id) }}">
      <input type="text" name="operator" placeholder="your name" value="operator">
      <input type="text" name="note" placeholder="what you did (optional)" size="40">
      <button type="submit">Resume run</button>
    </form>
  </div>
{% endfor %}

<h2>Recent ({{ history|length }})</h2>
{% for e in history %}
  <div class="esc done">
    <h2>#{{ e.id }} &middot; {{ e.capability_id }} &middot;
        <code>{{ e.status }}</code></h2>
    <dl>
      <dt>run</dt><dd><code>{{ e.run_id }}</code></dd>
      <dt>step</dt><dd>{{ e.step_ordinal }} (<code>{{ e.step_id }}</code>),
                       trigger <code>{{ e.trigger }}</code></dd>
      {% if e.status == 'resumed' %}
        <dt>resumed by</dt><dd>{{ e.resumed_by }} at {{ e.resumed_at }}</dd>
        <dt>note</dt><dd>{{ e.operator_note or "—" }}</dd>
      {% endif %}
    </dl>
  </div>
{% endfor %}
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Minimal replay-escalation operator console")
    p.add_argument("--port", type=int, default=5002)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--db", default=str(REPO_ROOT / "evidence" / "sessions"
                                      / "escalations.db"))
    p.add_argument("--evidence-replays",
                   default=str(REPO_ROOT / "evidence" / "replays"))
    args = p.parse_args(argv)
    app = create_app(db_path=args.db, evidence_replays_dir=args.evidence_replays)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
