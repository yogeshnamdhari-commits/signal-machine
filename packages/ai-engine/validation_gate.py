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
        root / "config" / "environment_contract.py",
        root / "config" / "runtime_defaults.py",
        root / "config" / "schema.py",
        root / "execution" / "risk_integrity.py",
        root / "validation" / "certification.py",
        root / "validation" / "statistical_validation.py",
        root / "validation" / "live_readiness.py",
        root / "validation" / "live_gate.py",
    ]
    for path in required:
        if not path.exists():
            failures.append(f"missing required contract module: {path}")

    adapter = root / "exchanges" / "binance_ws.py"
    if adapter.exists():
        text = adapter.read_text(encoding="utf-8")
        if '"_source": "ticker_arr"' in text or "Generate synthetic trade events" in text:
            failures.append("canonical Binance adapter still contains ticker-to-trade synthetic injection")

    if "depth@100ms" not in config.scanner.ws_streams:
        failures.append("effective scanner configuration lacks L2 depth@100ms feed")

    backend_index = repo / "packages" / "backend" / "src" / "index.ts"
    backend_routes = repo / "packages" / "backend" / "src" / "routes" / "index.ts"
    if backend_index.exists():
        text = backend_index.read_text(encoding="utf-8")
        if "signalEngine.startContinuousScan" in text or "tradeSimulator.start()" in text:
            failures.append("Node backend still starts an independent executable trading authority")
        for marker in ["PYTHON_CANONICAL_AUTHORITY", "/indicators/signal", "/risk/position/size", "/scanner/scan"]:
            if marker not in text:
                failures.append(f"Node canonical-authority guard missing: {marker}")
    if backend_routes.exists():
        text = backend_routes.read_text(encoding="utf-8")
        for marker in ["dataQuality: 'UNAVAILABLE'", "provenance: 'binance_aggTrades'", "PYTHON_CANONICAL_AUTHORITY"]:
            if marker not in text:
                failures.append(f"Node route provenance/authority marker missing: {marker}")
        if "takerBuyVol: vol * 0.5" in text or "takerSellVol: vol * 0.5" in text or "buyRatio: 0.5" in text:
            failures.append("Node orderflow contains a synthetic 50/50 fallback")

    main_py = root / "main.py"
    if main_py.exists():
        text = main_py.read_text(encoding="utf-8")
        if "apply_risk_integrity_patch()" not in text:
            failures.append("risk integrity gate is not installed before engine startup")
        if '"live"' not in text or "verify_live_certification" not in text:
            failures.append("live mode is not fail-closed behind current certification")

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
