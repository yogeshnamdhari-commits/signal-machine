import asyncio

import pytest

from scanner.signal_router import SignalRouter


@pytest.mark.asyncio
async def test_elite_persisted_signal_is_published_to_execution_bus(monkeypatch):
    import scanner.signal_router as module

    saved = []
    published = []

    async def save_signal(sig, is_elite=False):
        saved.append((sig.copy(), is_elite))
        return 123

    async def send_alert(_sig):
        return None

    async def publish(topic, sig):
        published.append((topic, sig.copy()))

    monkeypatch.setattr(module.repo, "save_signal", save_signal)
    monkeypatch.setattr(module.SignalRouter.__init__, "__defaults__", None, raising=False)
    router = SignalRouter()
    monkeypatch.setattr(router.telegram, "send_elite_alert", send_alert)
    monkeypatch.setattr(module.bus, "publish", publish)

    signal = {
        "symbol": "BTCUSDT",
        "type": "LONG",
        "confidence": 0.90,
        "risk_reward": 2.5,
        "mtf_alignment": 4,
        "institutional_score": 90,
        "entry_price": 65000,
        "stop_loss": 64000,
        "take_profit": 67000,
    }

    await router.route_signal(signal)

    assert saved and saved[0][1] is True
    assert published and published[0][0] == "execution_signal"
    assert published[0][1]["id"] == 123


@pytest.mark.asyncio
async def test_non_elite_signal_is_not_published_to_execution_bus(monkeypatch):
    import scanner.signal_router as module

    published = []

    async def save_signal(sig, is_elite=False):
        return 456

    async def publish(topic, sig):
        published.append((topic, sig.copy()))

    monkeypatch.setattr(module.repo, "save_signal", save_signal)
    router = SignalRouter()
    monkeypatch.setattr(module.bus, "publish", publish)

    signal = {
        "symbol": "BTCUSDT",
        "type": "LONG",
        "confidence": 0.84,
        "risk_reward": 2.5,
        "mtf_alignment": 4,
        "institutional_score": 90,
    }

    await router.route_signal(signal)

    assert published == []
