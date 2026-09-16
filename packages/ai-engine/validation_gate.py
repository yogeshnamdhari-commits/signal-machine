"""Repository-level validation gate used by CI before dev can sync to main."""
from __future__ import annotations

import ast
from pathlib import Path

from config import config
from config.environment_contract import validate_runtime_config


def main() -> None:
    root = Path(__file__).resolve().parent
    repo = root.parent.parent
    failures = []

    try:
        validate_runtime_config(config)
    except Exception as exc:
        failures.append(f"configuration contract: {exc}")

    required = [
        root / "core" / "provenance.py",
        root / "core" / "market_data.py",
        root / "core" / "signal_contract.py",
        root / "core" / "signal_consensus.py",
        root / "core" / "directional_factor.py",
        root / "core" / "parameter_semantics.py",
        root / "core" / "signal_provenance.py",
        root / "config" / "environment_contract.py",
        root / "config" / "runtime_defaults.py",
        root / "config" / "schema.py",
        root / "execution" / "risk_integrity.py",
        root / "validation" / "certification.py",
        root / "validation" / "statistical_validation.py",
        root / "validation" / "live_readiness.py",
        root / "validation" / "live_gate.py",
        root / "dashboard" / "live_sheet_contract.py",
    ]
    for path in required:
        if not path.exists():
            failures.append(f"missing required contract module: {path}")

    adapter = root / "exchanges" / "binance_ws.py"
    if adapter.exists():
        text = adapter.read_text(encoding="utf-8")
        if '"_source": "ticker_arr"' in text or "Generate synthetic trade events" in text:
            failures.append("canonical Binance adapter still contains ticker-to-trade synthetic injection")
        if '"feed": "ticker24h"' in text and 'await self._callback("trade"' in text:
            failures.append("ticker24h is still capable of producing trade callbacks")

    if "depth@100ms" not in config.scanner.ws_streams:
        failures.append("effective scanner configuration lacks L2 depth@100ms feed")
    total_streams = config.scanner.max_symbols * len(config.scanner.ws_streams) + 1 + len(config.scanner.global_streams)
    if total_streams > 1024:
        failures.append(f"effective Binance stream budget exceeded: {total_streams}>1024")
    if config.binance.testnet and config.binance.ws_url != config.binance.ws_production:
        failures.append("market-data WebSocket must remain on production Binance feed")

    backend_index = repo / "packages" / "backend" / "src" / "index.ts"
    backend_routes = repo / "packages" / "backend" / "src" / "routes" / "index.ts"
    if backend_index.exists():
        text = backend_index.read_text(encoding="utf-8")
        if "signalEngine.startContinuousScan" in text or "tradeSimulator.start()" in text:
            failures.append("Node backend still starts an independent executable trading authority")
        for marker in ["CANONICAL_SIGNAL_AUTHORITY", "canonicalReadOnlyGuard", "authority: 'python'", "PYTHON_CANONICAL_AUTHORITY"]:
            if marker not in text:
                failures.append(f"Node entrypoint guard missing: {marker}")
    if backend_routes.exists():
        text = backend_routes.read_text(encoding="utf-8")
        for marker in ["PYTHON_CANONICAL_AUTHORITY", "dataQuality: 'UNAVAILABLE'", "provenance: 'binance_aggTrades'", "provenance: 'aggTrades_request_failed'"]:
            if marker not in text:
                failures.append(f"Node route provenance/authority marker missing: {marker}")
        if "takerBuyVol: vol * 0.5" in text or "takerSellVol: vol * 0.5" in text or "buyRatio: 0.5" in text:
            failures.append("Node orderflow contains a synthetic 50/50 fallback")
        for forbidden in [
            "router.post('/signals/scan'",
            "router.put('/signals/:id/status'",
            "router.post('/indicators/signal'",
            "router.put('/risk/params'",
            "router.post('/risk/position/check'",
            "router.post('/risk/position/size'",
            "router.post('/scanner/scan'",
            "router.post('/simulator/reset'",
        ]:
            if forbidden not in text:
                failures.append(f"Node route authority boundary missing: {forbidden}")

    for dashboard_path in [root / "dashboard" / "app.py", root / "dashboard" / "pages" / "1_Live_Sheet.py"]:
        if dashboard_path.exists():
            text = dashboard_path.read_text(encoding="utf-8").lower()
            for forbidden in ("long (implied)", "short (implied)", "_compute_implied_signal", "_signal_implied"):
                if forbidden in text:
                    failures.append(f"dashboard still contains implied executable signal logic: {dashboard_path}:{forbidden}")

    main_py = root / "main.py"
    if main_py.exists():
        text = main_py.read_text(encoding="utf-8")
        if "apply_risk_integrity_patch()" not in text:
            failures.append("risk integrity gate is not installed before engine startup")
        if '"live"' not in text or "verify_live_certification" not in text:
            failures.append("live mode is not fail-closed behind current certification")
        if "reload=False" not in text:
            failures.append("API server must not run with autoreload in production")

    for path in root.rglob("*.py"):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            failures.append(f"AST parse failure {path}: {exc}")

    if failures:
        raise SystemExit("Repository validation failed:\n- " + "\n- ".join(failures))
    print("Repository validation gate passed.")


if __name__ == "__main__":
    main()
