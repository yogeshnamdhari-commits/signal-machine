import hashlib
import json

import pytest

from validation.forward_evidence_binding import (
    ForwardEvidenceBindingError,
    verify_forward_evidence_report,
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _write_report(tmp_path, **overrides):
    report = {
        "schema_version": 1,
        "status": "EVIDENCE_VALID",
        "authorization": "NOT_A_LIVE_CERTIFICATION",
        "sessions": ["C", "D"],
        "code_commit_sha": "commit-a",
        "parameter_hash": "param-1",
        "total_signals": 500,
        "total_closed_trades": 100,
        "win_rate": 0.55,
        "profit_factor": 1.50,
        "expectancy": 1.25,
        "net_pnl": 125.0,
        "total_gross_pnl": 200.0,
        "total_fees": 50.0,
        "total_funding_pnl": -5.0,
        "total_slippage": 20.0,
        "max_drawdown_pct": 8.0,
        "bundle_roots": {
            "C": "forward_sessions/session_C_evidence",
            "D": "forward_sessions/session_D_evidence",
        },
    }
    report.update(overrides)
    report["aggregate_sha256"] = hashlib.sha256(
        _canonical(report).encode("utf-8")
    ).hexdigest()
    path = tmp_path / "forward_aggregate.json"
    path.write_bytes(json.dumps(report, sort_keys=True, indent=2).encode("utf-8"))
    return path


def test_forward_evidence_binding_accepts_valid_report(tmp_path):
    path = _write_report(tmp_path)
    report = verify_forward_evidence_report(path, expected_commit="commit-a")
    assert report["_file_sha256"]
    assert report["total_closed_trades"] == 100


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"aggregate_sha256": "0" * 64}, "integrity hash mismatch"),
        ({"total_closed_trades": 99}, "trade threshold"),
        ({"total_signals": 499}, "signal threshold"),
        ({"profit_factor": 1.20}, "profit-factor threshold"),
        ({"expectancy": 0.0}, "expectancy is not positive"),
        ({"net_pnl": 0.0}, "net PnL is not positive"),
        ({"authorization": "LIVE_CERTIFICATION"}, "authorization marker"),
    ],
)
def test_forward_evidence_binding_rejects_invalid_report(tmp_path, overrides, match):
    path = _write_report(tmp_path, **overrides)
    if "aggregate_sha256" not in overrides:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        artifact["aggregate_sha256"] = hashlib.sha256(
            _canonical({k: v for k, v in artifact.items() if k != "aggregate_sha256"}).encode("utf-8")
        ).hexdigest()
        path.write_text(json.dumps(artifact, sort_keys=True, indent=2), encoding="utf-8")
    with pytest.raises(ForwardEvidenceBindingError, match=match):
        verify_forward_evidence_report(path, expected_commit="commit-a")


def test_forward_evidence_binding_rejects_commit_mismatch(tmp_path):
    path = _write_report(tmp_path, code_commit_sha="different")
    with pytest.raises(ForwardEvidenceBindingError, match="commit does not match"):
        verify_forward_evidence_report(path, expected_commit="commit-a")
