import json


def test_bridge_writer_marks_python_signals_canonical(tmp_path, monkeypatch):
    import dashboard.data_bridge as data_bridge

    signals_path = tmp_path / "signals.json"
    monkeypatch.setattr(data_bridge, "SIGNALS_FILE", signals_path)

    data_bridge.BridgeWriter().write_signals([
        {"symbol": "BTCUSDT", "side": "BUY", "score": 92},
    ])

    payload = json.loads(signals_path.read_text())
    signal = payload["signals"][0]
    assert signal["source"] == "python_engine"
    assert signal["authority"] == "python"
    assert signal["canonical"] is True
    assert signal["provenance_ids"] == []
