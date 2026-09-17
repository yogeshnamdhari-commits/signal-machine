import sqlite3
from pathlib import Path

from scanner.deployment_gate import DeploymentGate


def test_deployment_gate_imports_and_missing_database_fails_closed(tmp_path: Path):
    result = DeploymentGate(tmp_path / "missing-forward-test.db").evaluate()

    assert result["status"] == "DO NOT DEPLOY"
    assert result["passed"] == 0
    assert result["total"] == 5
    assert "forward_test.db does not exist" in result["reason"]


def test_malformed_forward_database_fails_closed(tmp_path: Path):
    db_path = tmp_path / "malformed-forward-test.db"
    sqlite3.connect(db_path).close()

    result = DeploymentGate(db_path).evaluate()

    assert result["status"] == "DO NOT DEPLOY"
    assert result["passed"] == 0
    assert result["total"] == 5
    assert "invalid forward-test database" in result["reason"]


def test_non_finite_forward_pnl_fails_closed(tmp_path: Path):
    db_path = tmp_path / "non-finite-forward-test.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE forward_signals (id INTEGER)")
        db.execute("CREATE TABLE forward_trades (outcome TEXT, net_pnl REAL)")
        db.execute("INSERT INTO forward_signals VALUES (1)")
        db.execute("INSERT INTO forward_trades VALUES ('closed', 1e309)")

    result = DeploymentGate(db_path).evaluate()

    assert result["status"] == "DO NOT DEPLOY"
    assert result["passed"] == 0
    assert result["total"] == 5
    assert "non-finite PnL aggregates" in result["reason"]
