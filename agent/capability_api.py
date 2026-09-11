"""Agent-facing capability interface (Section 8 stretch goal).

Wraps the existing replay engine as something an AI agent can discover
and invoke by name with typed args, per Section 1's framing -- no new
execution logic, just a thin HTTP layer over ``agent.replay.Replayer``,
the same engine ``agent.replay_cli`` already drives.

    GET  /capabilities                  -- list the served catalog
    POST /capabilities/<id>/invoke      -- run a capability, synchronously

The served catalog is ``capabilities/*.capability.json`` (a peer of
``agent/``, ``evidence/``, ``schema/``, ``target_app/``) -- not
``evidence/compiled/``, which stays curated submission proof and is
untouched by this module.

Handoff is always disabled here (``handoff_enabled=False``): an HTTP
request blocking for minutes on a human operator is the wrong shape
regardless of whether the underlying mechanism works. On an
``escalate`` trigger this returns a clean ``PendingEscalation`` body
immediately; the real escalation still opens in the SessionStore and
is visible to the existing operator console (``agent.operator_console``)
for anyone polling it separately.

Run:
    python -m agent.capability_api --port 5003
"""
from __future__ import annotations

import argparse
from pathlib import Path

from flask import Flask, jsonify, request

from agent.models import Capability, InputParam, OutputParam
from agent.replay import BusinessOutcome, HardFailure, PendingEscalation, Replayer, Success

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CAPABILITIES_DIR = REPO_ROOT / "capabilities"
DEFAULT_EVIDENCE_ROOT = REPO_ROOT / "evidence" / "replays"
DEFAULT_SESSION_DB_PATH = REPO_ROOT / "evidence" / "sessions" / "escalations.db"

# result type -> HTTP status. Success and BusinessOutcome are both
# legitimate answers to a well-formed request (200). HardFailure is a
# well-formed request replay could not complete (422, not 5xx -- the
# server isn't broken). PendingEscalation is accepted-but-not-done: the
# request was valid, a human step is now required (202).
_STATUS_BY_RESULT_TYPE = {
    Success: 200,
    BusinessOutcome: 200,
    HardFailure: 422,
    PendingEscalation: 202,
}


def _load_catalog(capabilities_dir: Path) -> dict[str, Capability]:
    """Scan ``capabilities_dir`` for ``*.capability.json`` and load each.

    Re-scanned on every request rather than cached at startup, so the
    catalog reflects whatever is on disk right now -- consistent with
    how ``replay_cli`` re-reads the artifact on every invocation.
    """
    catalog: dict[str, Capability] = {}
    for path in sorted(capabilities_dir.glob("*.capability.json")):
        cap = Capability.model_validate_json(path.read_text())
        catalog[cap.capability_id] = cap
    return catalog


def _trim_input(p: InputParam) -> dict:
    d = {
        "name": p.name,
        "type": p.type.value,
        "required": p.required,
        "description": p.description,
    }
    if p.allowed_values:
        d["allowed_values"] = p.allowed_values
    return d


def _trim_output(p: OutputParam) -> dict:
    return {
        "name": p.name,
        "type": p.type.value,
        "required": p.required,
        "description": p.description,
    }


def _trim_capability(cap: Capability) -> dict:
    """The discovery-listing shape: enough for an agent to decide what to
    call and how, deliberately excluding internal fields (steps,
    guardrails, escalation_policy, discovery provenance) it doesn't need.
    """
    return {
        "capability_id": cap.capability_id,
        "version": cap.version,
        "description": cap.description,
        "risk_class": cap.risk_class,
        "requires_confirmation": cap.requires_confirmation,
        "inputs": [_trim_input(p) for p in cap.inputs],
        "outputs": [_trim_output(p) for p in cap.outputs],
    }


def create_app(
    capabilities_dir: str | Path = DEFAULT_CAPABILITIES_DIR,
    evidence_root: str | Path = DEFAULT_EVIDENCE_ROOT,
    session_db_path: str | Path = DEFAULT_SESSION_DB_PATH,
) -> Flask:
    app = Flask(__name__)
    app.config["CAPABILITIES_DIR"] = Path(capabilities_dir)
    app.config["EVIDENCE_ROOT"] = str(evidence_root)
    app.config["SESSION_DB_PATH"] = str(session_db_path)

    @app.get("/capabilities")
    def list_capabilities():
        catalog = _load_catalog(app.config["CAPABILITIES_DIR"])
        return jsonify([_trim_capability(cap) for cap in catalog.values()])

    @app.post("/capabilities/<capability_id>/invoke")
    def invoke(capability_id: str):
        catalog = _load_catalog(app.config["CAPABILITIES_DIR"])
        cap = catalog.get(capability_id)
        if cap is None:
            return jsonify({"error": f"unknown capability_id {capability_id!r}"}), 404

        body = request.get_json(silent=True)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        confirmed = bool(body.pop("confirmed", False))
        inputs = {k: str(v) for k, v in body.items()}

        replayer = Replayer(
            cap,
            evidence_root=app.config["EVIDENCE_ROOT"],
            handoff_enabled=False,
            confirmed=confirmed,
            session_db_path=app.config["SESSION_DB_PATH"],
        )
        result = replayer.run(inputs)
        status = _STATUS_BY_RESULT_TYPE[type(result)]
        return jsonify(result.to_dict()), status

    return app


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Agent-facing capability interface")
    p.add_argument("--port", type=int, default=5003)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--capabilities-dir", default=str(DEFAULT_CAPABILITIES_DIR))
    p.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE_ROOT))
    p.add_argument("--session-db", default=str(DEFAULT_SESSION_DB_PATH))
    args = p.parse_args(argv)
    app = create_app(
        capabilities_dir=args.capabilities_dir,
        evidence_root=args.evidence_root,
        session_db_path=args.session_db,
    )
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
