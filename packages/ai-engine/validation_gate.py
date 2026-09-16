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
        forbidden = ('"_source": "ticker_arr"', "Generate synthetic trade events", 'await self._callback("trade", trade)\n\n    async def _on_ticker_arr')
        if any(token in text for token in forbidden[:2]):
            failures.append("canonical Binance adapter still contains ticker-to-trade synthetic injection")

    scanner_config = config.scanner.ws_streams
    if "depth@100ms" not in scanner_config:
        failures.append("effective scanner configuration lacks L2 depth@100ms feed")

    backend_index = repo / "packages" / "backend" / "src" / "index.ts"
    if backend_index.exists():
        text = backend_index.read_text(encoding="utf-8")
        if "signalEngine.startContinuousScan" in text or "tradeSimulator.start()" in text:
            failures.append("Node backend still starts an independent executable trading authority")
        required_guards = [
            "PYTHON_CANONICAL_AUTHORITY",
            "POST /api/signals/scan",
            "/indicators/signal",
            "/risk/position/size",
            "/scanner/scan",
        ]
        for marker in required_guards:
            if marker not in text:
                failures.append(f"Node canonical-authority guard missing: {marker}")

    main_py = root / "main.py"
    if main_py.exists():
        text = main_py.read_text(encoding="utf-8")
        if "apply_risk_integrity_patch()" not in text:
            failures.append("risk integrity gate is not installed before engine startup")
        if '"live"' not in text or "verify_live_certification" not in text:
            failures.append("live mode is not fail-closed behind current certification")

    # Basic parse check for every Python module; compile is separately run in CI.
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
