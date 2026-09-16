import asyncio

import pytest


def test_execution_engine_rejects_uncertified_signal(monkeypatch):
    from execution import execution_engine
    from validation.live_gate import LiveCertificationError

    class Audit:
        def __init__(self):
            self.rejections = []

        async def signal_rejected(self, signal_id, reason):
            self.rejections.append((signal_id, reason))

    def deny():
        raise LiveCertificationError("no current matching certification")

    monkeypatch.setattr(execution_engine, "require_live_certification", deny)
    engine = object.__new__(execution_engine.ExecutionEngine)
    engine.audit = Audit()
    engine._signal_count = 0

    async def run():
        result = await engine.on_signal({
            "id": "sig-1",
            "symbol": "BTCUSDT",
            "type": "LONG",
            "entry_price": 100,
            "confidence": 0.95,
            "regime": "bull",
            "institutional_score": 95,
            "stop_loss": 98,
            "take_profit": 104,
        })
        return result

    assert asyncio.run(run()) is None
    assert engine._signal_count == 0
    assert engine.audit.rejections
