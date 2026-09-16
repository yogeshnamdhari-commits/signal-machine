"""Repository-level validation gate used by CI before dev can sync to main."""
from __future__ import annotations

import ast
from pathlib import Path

from config import config
from config.environment_contract import validate_runtime_config


def main() -> None:
    root = Path(__file__).resolve().parent
    failures = []

    try:
        validate_runtime_config(config)
    except Exception as exc:
        failures.append(f"configuration contract: {exc}")

    required = [
        root / "core" / "provenance.py",
        root / "core" / "market_data.py",
        root / "config" / "environment_contract.py",
        root / "config" / "schema.py",
        root / "validation" / "certification.py",
        root / "validation" / "statistical_validation.py",
        root / "validation" / "live_readiness.py",
    ]
    for path in required:
        if not path.exists():
            failures.append(f"missing required contract module: {path}")

    # Guard against direct synthetic trade creation in the canonical Binance adapter.
    adapter = root / "exchanges" / "binance_ws.py"
    if adapter.exists():
        text = adapter.read_text(encoding="utf-8")
        if '"_source": "ticker_arr"' in text or "Generate synthetic trade events" in text:
            failures.append("canonical Binance adapter still contains ticker-to-trade synthetic injection")

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
