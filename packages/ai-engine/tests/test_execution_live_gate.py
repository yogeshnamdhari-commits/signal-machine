import asyncio


def test_execution_engine_blocks_signal_without_live_certification(monkeypatch):
    from execution import execution_engine

    class _Denied:
        @staticmethod
        def verify_live_certification():
            raise RuntimeError("no current certification")

    monkeypatch.setattr(execution_engine, "_LIVE_GATE_TEST_OVERRIDE", _Denied, raising=False)

    engine = object.__new__(execution_engine.ExecutionEngine)
    engine.audit = None
    engine._signal_count = 0

    async def _run():
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
        assert result is None
        assert engine._signal_count == 0

    asyncio.run(_run())
