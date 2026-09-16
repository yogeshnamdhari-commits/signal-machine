import pytest

from execution.live_execution_gate import require_live_certification


def test_live_execution_gate_is_fail_closed(monkeypatch):
    import execution.live_execution_gate as gate
    from validation.live_gate import LiveCertificationError

    def deny():
        raise LiveCertificationError("no current matching certification")

    monkeypatch.setattr(gate, "verify_live_certification", deny)
    with pytest.raises(LiveCertificationError, match="live execution certification gate"):
        require_live_certification()
