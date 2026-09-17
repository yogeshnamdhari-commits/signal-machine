from pathlib import Path

from scanner.deployment_gate import DeploymentGate


def test_deployment_gate_imports_and_missing_database_fails_closed(tmp_path: Path):
    result = DeploymentGate(tmp_path / "missing-forward-test.db").evaluate()

    assert result["status"] == "DO NOT DEPLOY"
    assert result["passed"] == 0
    assert result["total"] == 5
    assert "forward_test.db does not exist" in result["reason"]
