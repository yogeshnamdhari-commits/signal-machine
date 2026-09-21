from pathlib import Path

import pytest

from app_layer.forward_evidence_aggregator import ForwardEvidenceError, _read_signal_ids


def test_rejected_signal_requires_attribution_reason(tmp_path: Path):
    path = tmp_path / "signals.csv"
    path.write_text(
        "id,timestamp,symbol,side,entry_price,stop_loss,take_profit,status,rejection_reason\n"
        "sig-1,1000,BTCUSDT,LONG,100,99,102,rejected,\n",
        encoding="utf-8",
    )

    with pytest.raises(ForwardEvidenceError, match="rejected signal without a rejection reason"):
        _read_signal_ids(path)


def test_rejected_signal_with_reason_is_attributable(tmp_path: Path):
    path = tmp_path / "signals.csv"
    path.write_text(
        "id,timestamp,symbol,side,entry_price,stop_loss,take_profit,status,rejection_reason\n"
        "sig-1,1000,BTCUSDT,LONG,100,99,102,rejected,insufficient liquidity\n",
        encoding="utf-8",
    )

    assert _read_signal_ids(path) == {"sig-1"}
