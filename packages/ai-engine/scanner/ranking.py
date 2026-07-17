"""
TOP-10 Ranking Engine — composite score, multi-criteria ranking.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from loguru import logger


class RankingEngine:
    def __init__(self) -> None:
        self.weights = {
            "confidence": 0.35,
            "risk_reward": 0.20,
            "volume": 0.15,
            "institutional": 0.15,
            "regime_fit": 0.10,
            "freshness": 0.05,
        }

    def rank_signals(self, signals: List[Dict]) -> List[Dict]:
        active = [s for s in signals if s.get("status") == "active"]
        if not active:
            return []
        for s in active:
            s["composite_score"] = self._score(s)
        ranked = sorted(active, key=lambda x: x["composite_score"], reverse=True)
        for i, s in enumerate(ranked[:10]):
            s["rank"] = i + 1
        return ranked[:10]

    def _score(self, sig: Dict) -> float:
        scores: Dict[str, float] = {}
        scores["confidence"] = sig.get("confidence", 0)

        entry = sig.get("entry_price", 0)
        sl = sig.get("stop_loss", 0)
        tp = sig.get("take_profit", 0)
        if all([entry, sl, tp]):
            risk = abs(entry - sl)
            rr = abs(tp - entry) / risk if risk else 1
            scores["risk_reward"] = min(rr / 3, 1)
        else:
            scores["risk_reward"] = 0.5

        of = sig.get("orderflow", {})
        scores["volume"] = min(abs(of.get("volume_trend", 0)) / 100_000, 1) if of else 0.3

        inst = sig.get("institutional", [])
        if inst:
            scores["institutional"] = max(p.get("confidence", 0) for p in inst)
        else:
            scores["institutional"] = 0.2

        regime = sig.get("regime", "")
        good = {"LONG": ["trending_up", "breakout", "reversal"], "SHORT": ["trending_down", "breakout", "reversal"]}
        scores["regime_fit"] = 0.8 if regime in good.get(sig.get("type", ""), []) else 0.4

        age = time.time() - sig.get("created_at", time.time())
        scores["freshness"] = max(1 - age / 300, 0)

        return sum(scores.get(k, 0) * v for k, v in self.weights.items())
