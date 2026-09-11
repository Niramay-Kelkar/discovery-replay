"""Tests for the agent-facing capability interface (agent/capability_api.py).

The catalog listing and the confirmation-gate case need no browser: the
listing is pure JSON loading, and the confirmation gate fails in
``Replayer._preflight`` before Playwright ever launches, testable the
same way ``test_replay.py``'s own preflight tests are. The
real-invoke-returns-Success case genuinely drives ``target_app`` through
a live browser (started in-process for the test), matching what
``evidence/README.md``'s replay runs already exercise via the CLI.
"""
import socket
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from agent.capability_api import create_app
from agent.models import Capability

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_DIR = REPO_ROOT / "capabilities"
CAP_PATH = CAPABILITIES_DIR / "member_lookup.capability.json"


@pytest.fixture
def client(tmp_path):
    app = create_app(
        capabilities_dir=CAPABILITIES_DIR,
        evidence_root=str(tmp_path / "replays"),
        session_db_path=str(tmp_path / "escalations.db"),
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_list_capabilities_returns_trimmed_catalog(client):
    resp = client.get("/capabilities")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, list)
    entries = {c["capability_id"]: c for c in body}
    assert "member_lookup" in entries

    cap = entries["member_lookup"]
    assert set(cap) == {
        "capability_id", "version", "description", "risk_class",
        "requires_confirmation", "inputs", "outputs",
    }
    assert cap["version"] == "1.1.0"
    assert cap["risk_class"] == "read_only"
    assert cap["requires_confirmation"] is False

    input_names = {i["name"] for i in cap["inputs"]}
    assert input_names == {"search_field", "search_term"}
    search_field = next(i for i in cap["inputs"] if i["name"] == "search_field")
    assert search_field["type"] == "string"
    assert search_field["required"] is True
    assert search_field["allowed_values"] == ["Member ID", "Last name"]

    output_names = {o["name"] for o in cap["outputs"]}
    assert output_names == {"full_name", "savings_balance", "outcome_code"}


def test_invoke_unknown_capability_returns_404(client):
    resp = client.post("/capabilities/does_not_exist/invoke", json={})
    assert resp.status_code == 404


def test_invoke_without_confirmation_on_a_gated_capability_returns_422(
    client, tmp_path, monkeypatch
):
    # member_lookup itself doesn't require confirmation -- reuse the same
    # synthetic-fixture pattern as test_replay.py's own confirmation test:
    # a copy of the artifact with requires_confirmation forced True.
    cap = Capability.model_validate_json(CAP_PATH.read_text())
    gated = cap.model_copy(update={"requires_confirmation": True})

    gated_dir = tmp_path / "capabilities"
    gated_dir.mkdir()
    (gated_dir / "member_lookup.capability.json").write_text(gated.model_dump_json())

    app = create_app(
        capabilities_dir=gated_dir,
        evidence_root=str(tmp_path / "replays"),
        session_db_path=str(tmp_path / "escalations.db"),
    )
    app.config["TESTING"] = True
    gated_client = app.test_client()

    resp = gated_client.post(
        "/capabilities/member_lookup/invoke",
        json={"search_field": "Member ID", "search_term": "M1001"},
    )
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["status"] == "hard_failure"
    assert body["trigger"] == "confirmation_required"

    # with confirmed=True, preflight's confirmation gate no longer fires --
    # it still won't reach a live target_app in this test, so it fails
    # downstream (no browser/network), but not on confirmation_required.
    resp2 = gated_client.post(
        "/capabilities/member_lookup/invoke",
        json={"search_field": "Member ID", "search_term": "M1001", "confirmed": True},
    )
    assert resp2.status_code in (200, 422, 202)
    if resp2.status_code == 422:
        assert resp2.get_json()["trigger"] != "confirmation_required"


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


@pytest.fixture(scope="module")
def live_target_app():
    """Boots target_app in-process on its default base_url (127.0.0.1:5001,
    matching capabilities/member_lookup.capability.json's target.base_url)
    so the Success-path test drives a real browser against a real server,
    not a mock. Reuses an already-running instance if one is up (e.g.
    started manually for the Step 4 live demo).
    """
    from target_app.app import app as target_flask_app

    host, port = "127.0.0.1", 5001
    if _port_open(host, port):
        yield f"http://{host}:{port}"
        return

    server = make_server(host, port, target_flask_app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_invoke_real_capability_against_live_target_app_returns_success(
    client, live_target_app
):
    resp = client.post(
        "/capabilities/member_lookup/invoke",
        json={"search_field": "Member ID", "search_term": "M1001"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["outputs"]["full_name"]
    assert body["outputs"]["outcome_code"] == "SUCCESS"
