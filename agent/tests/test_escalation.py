"""Escalation store + operator console tests. Offline: no browser, no API."""
from pathlib import Path

import pytest

from agent.escalation import SessionStore
from agent.operator_console import create_app


@pytest.fixture
def store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / "escalations.db")


def _open(store, **over):
    kw = dict(
        run_id="replay-x", capability_id="member_lookup", step_id="click_open",
        step_ordinal=4, trigger="on_unrecognized_dialog", phase="check",
        expected="record table visible", observed="unrecognized alertdialog",
        screenshot_path="/tmp/x.png",
    )
    kw.update(over)
    return store.open_escalation(**kw)


def test_open_and_read_back(store):
    eid = _open(store)
    rec = store.get(eid)
    assert rec["status"] == "pending"
    assert rec["trigger"] == "on_unrecognized_dialog"
    assert rec["step_ordinal"] == 4
    assert store.list_pending()[0]["id"] == eid
    assert store.list_for_run("replay-x")[0]["id"] == eid


def test_unknown_trigger_rejected(store):
    with pytest.raises(ValueError):
        _open(store, trigger="on_vibes")


def test_resume_is_one_shot(store):
    eid = _open(store)
    assert store.mark_resumed(eid, resumed_by="dana", operator_note="cleared hold") is True
    # a second resume does nothing (already not pending)
    assert store.mark_resumed(eid) is False
    rec = store.get(eid)
    assert rec["status"] == "resumed"
    assert rec["resumed_by"] == "dana"
    assert rec["operator_note"] == "cleared hold"
    assert store.list_pending() == []


def test_timeout_only_affects_pending(store):
    eid = _open(store)
    store.mark_resumed(eid)
    store.mark_timed_out(eid)  # must not clobber a resume
    assert store.get(eid)["status"] == "resumed"


# --- operator console -----------------------------------------------------

@pytest.fixture
def console(tmp_path):
    db = tmp_path / "escalations.db"
    replays = tmp_path / "replays"
    store = SessionStore(db)
    # a real screenshot file for run A
    shot_a = replays / "replay-A" / "screenshots" / "escalation_step04_click_open.png"
    shot_a.parent.mkdir(parents=True)
    shot_a.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    eid = store.open_escalation(
        run_id="replay-A", capability_id="member_lookup", step_id="click_open",
        step_ordinal=4, trigger="on_unrecognized_dialog", phase="check",
        expected="x", observed="y", screenshot_path=str(shot_a),
    )
    app = create_app(db_path=str(db), evidence_replays_dir=str(replays))
    app.config.update(TESTING=True)
    return app.test_client(), store, eid, shot_a.name


def test_index_lists_pending(console):
    client, _store, eid, _name = console
    body = client.get("/").get_data(as_text=True)
    assert f"#{eid}" in body
    assert "on_unrecognized_dialog" in body


def test_screenshot_route_is_scoped_to_run_id(console):
    client, _store, _eid, name = console
    assert client.get(f"/screenshot/replay-A/{name}").status_code == 200
    # a different run_id has no escalation -> refused even for the same file
    assert client.get(f"/screenshot/replay-B/{name}").status_code == 404
    # traversal is refused
    assert client.get("/screenshot/replay-A/..%2f..%2fsecret.png").status_code == 404


def test_resume_unblocks_via_store(console):
    client, store, eid, _name = console
    assert store.get(eid)["status"] == "pending"
    resp = client.post(f"/resume/{eid}", data={"operator": "sam", "note": "dismissed hold"})
    assert resp.status_code in (302, 303)
    rec = store.get(eid)
    assert rec["status"] == "resumed"
    assert rec["resumed_by"] == "sam"
