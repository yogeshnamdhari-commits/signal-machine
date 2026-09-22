#!/usr/bin/env python3
"""Export the canonical 60-symbol production live-sheet snapshot.

Reads only the canonical Python engine bridge. It never fetches or invents market
data itself. Run while main.py --mode engine is producing a fresh bridge.
"""
from __future__ import annotations

import csv
import json
import subprocess
import time
from pathlib import Path

from dashboard.live_sheet_contract import (
    build_signal_display,
    display_value,
    live_observation_values,
)

REQUESTED_SYMBOLS = [
    "ZECUSDT","UNIUSDT","ONDOUSDT","龙虾USDT","XTZUSDT","PENGUUSDT",
    "USELESSUSDT","MARSCOINUSDT","SYNUSDT","SOLUSDT","TAOUSDT","XRPUSDT",
    "ZAMAUSDT","SUIUSDT","ONEUSDT","WLDUSDT","TRUMPUSDT","LSKUSDT",
    "XLMUSDT","LTCUSDT","XMRUSDT","PUMPUSDT","PONSUSDT","ZILUSDT",
    "VVVUSDT","STRKUSDT","牛来USDT","TRXUSDT","SKLUSDT","OPUSDT",
    "PIEVERSEUSDT","ONGUSDT","XPLUSDT","UBUSDT","MYXUSDT","RAYSOLUSDT",
    "TUSDT","SAGAUSDT","MUSDT","TIAUSDT","STXUSDT","WIFUSDT","WLFIUSDT",
    "PENDLEUSDT","MITOUSDT","VIRTUALUSDT","ZENUSDT","POLUSDT","MORPHOUSDT",
    "SEIUSDT","ZKUSDT","ZROUSDT","MONUSDT","VETUSDT","PTBUSDT","ORDIUSDT",
    "RENDERUSDT","SUSDT","SANDUSDT","STABLEUSDT",
]

COLUMNS = [
    "Symbol","Price","24h","Volume 24h","OI","OI Bias","OI Δ%","Funding","Fund Bias",
    "Net Delta","B/S Ratio","B/S","CVD 5m","CVD Bias","Flow Strength","Flow Bias",
    "Ex Flow","Vol Bias","Imbalance","Imb Bias","Liq Zone ↓","Liq Zone ↑","Liq Risk",
    "Sweep","Sweep Price","FVG","FVG Price","Regime","Reg Conf","Signal",
    "Signal Authority","Signal Reason",
]

ROOT = Path(__file__).resolve().parent
BRIDGE_DIR = ROOT / "data" / "bridge"
OUTPUT_DIR = ROOT.parent.parent / "live_snapshot"
MAX_SNAPSHOT_AGE_SECONDS = 10.0


def fmt(value, suffix=""):
    if value is None or value == "":
        return "UNAVAILABLE"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    magnitude = abs(number)
    if magnitude >= 1e9:
        return f"{number/1e9:.2f}B{suffix}"
    if magnitude >= 1e6:
        return f"{number/1e6:.2f}M{suffix}"
    if magnitude >= 1e3:
        return f"{number/1e3:.2f}K{suffix}"
    return f"{number:.4f}{suffix}"


def evidence(factor):
    return f"{factor.state.value} | {factor.quality.value}"


def git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "UNKNOWN"


def main() -> None:
    bridge_path = BRIDGE_DIR / "market_data.json"
    signals_path = BRIDGE_DIR / "signals.json"
    if not bridge_path.exists():
        raise SystemExit(f"Bridge unavailable: {bridge_path}")

    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    snapshot_ts = float(bridge.get("timestamp", 0) or 0)
    age = time.time() - snapshot_ts
    if snapshot_ts <= 0 or age < 0 or age > MAX_SNAPSHOT_AGE_SECONDS:
        raise SystemExit(
            f"Bridge is not fresh: timestamp={snapshot_ts} age={age:.2f}s "
            f"(max {MAX_SNAPSHOT_AGE_SECONDS}s)"
        )

    rows = {
        str(row.get("symbol")): dict(row)
        for row in bridge.get("rows", [])
        if row.get("symbol")
    }
    signals_payload = (
        json.loads(signals_path.read_text(encoding="utf-8"))
        if signals_path.exists()
        else {}
    )
    signals = {
        str(signal.get("symbol")): signal
        for signal in signals_payload.get("signals", [])
        if signal.get("symbol")
    }

    output = []
    quality_counts = {}
    for symbol in REQUESTED_SYMBOLS:
        row = rows.get(symbol)
        if row is None:
            output.append({
                "Symbol": symbol,
                "Price": "UNAVAILABLE",
                "24h": "UNAVAILABLE",
                "Volume 24h": "UNAVAILABLE",
                "OI": "UNAVAILABLE",
                "OI Bias": "NEUTRAL | NOT_APPLICABLE",
                "OI Δ%": "UNAVAILABLE",
                "Funding": "UNAVAILABLE",
                "Fund Bias": "NEUTRAL | NOT_APPLICABLE",
                "Net Delta": "UNAVAILABLE",
                "B/S Ratio": "UNAVAILABLE",
                "B/S": "NEUTRAL | NOT_APPLICABLE",
                "CVD 5m": "UNAVAILABLE",
                "CVD Bias": "NEUTRAL | NOT_APPLICABLE",
                "Flow Strength": "UNAVAILABLE",
                "Flow Bias": "NEUTRAL | NOT_APPLICABLE",
                "Ex Flow": "UNAVAILABLE",
                "Vol Bias": "NEUTRAL | NOT_APPLICABLE",
                "Imbalance": "UNAVAILABLE",
                "Imb Bias": "NEUTRAL | NOT_APPLICABLE",
                "Liq Zone ↓": "UNAVAILABLE",
                "Liq Zone ↑": "UNAVAILABLE",
                "Liq Risk": "UNAVAILABLE",
                "Sweep": "NEUTRAL | NOT_APPLICABLE",
                "Sweep Price": "UNAVAILABLE",
                "FVG": "NEUTRAL | NOT_APPLICABLE",
                "FVG Price": "UNAVAILABLE",
                "Regime": "NEUTRAL | NOT_APPLICABLE",
                "Reg Conf": "UNAVAILABLE",
                "Signal": "NO_SIGNAL",
                "Signal Authority": "none",
                "Signal Reason": "symbol unavailable on Binance production universe",
            })
            continue

        display = build_signal_display(signals.get(symbol, {}), row)
        observations = live_observation_values(row)
        fvg = display["fvg"]

        # Keep this audit visible in provenance rather than hiding unavailable data.
        for field_name in ("oi", "funding", "b_s_ratio", "cvd", "flow", "volume", "imbalance", "sweep", "regime"):
            q = getattr(display[field_name], "quality", None)
            if q is not None:
                quality_counts[q.value] = quality_counts.get(q.value, 0) + 1

        output.append({
            "Symbol": symbol,
            "Price": fmt(display_value(row, "price"), "$"),
            "24h": fmt(display_value(row, "change_24h"), "%"),
            "Volume 24h": fmt(display_value(row, "volume_24h"), "$"),
            "OI": fmt(display_value(row, "open_interest"), "$"),
            "OI Bias": evidence(display["oi"]),
            "OI Δ%": fmt(display_value(row, "oi_change_pct"), "%"),
            "Funding": fmt(display_value(row, "funding"), "%"),
            "Fund Bias": evidence(display["funding"]),
            "Net Delta": fmt(display_value(row, "net_delta"), "$"),
            "B/S Ratio": fmt(display_value(row, "buy_sell_ratio")),
            "B/S": evidence(display["b_s_ratio"]),
            "CVD 5m": fmt(observations["cvd_5m"]),
            "CVD Bias": evidence(display["cvd"]),
            "Flow Strength": fmt(observations["flow_strength"]),
            "Flow Bias": evidence(display["flow"]),
            "Ex Flow": fmt(observations["exchange_flow"], "$"),
            "Vol Bias": evidence(display["volume"]),
            "Imbalance": fmt(observations["imbalance"]),
            "Imb Bias": evidence(display["imbalance"]),
            "Liq Zone ↓": fmt(display_value(row, "long_liq_vol"), "$"),
            "Liq Zone ↑": fmt(display_value(row, "short_liq_vol"), "$"),
            "Liq Risk": str(display["liq_risk"] or "UNAVAILABLE").upper(),
            "Sweep": evidence(display["sweep"]),
            "Sweep Price": fmt(display["sweep_price"], "$"),
            "FVG": f"{fvg['state']} | {fvg['quality']}",
            "FVG Price": fmt(fvg["value"], "$"),
            "Regime": evidence(display["regime"]),
            "Reg Conf": fmt(display["regime_conf"], "%"),
            "Signal": display["signal"],
            "Signal Authority": display["authority"],
            "Signal Reason": display["signal_reason"],
        })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "live_snapshot.csv"
    md_path = OUTPUT_DIR / "live_snapshot.md"
    provenance_path = OUTPUT_DIR / "provenance.json"

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(output)

    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("| " + " | ".join(COLUMNS) + " |\n")
        handle.write("| " + " | ".join(["---"] * len(COLUMNS)) + " |\n")
        for row in output:
            handle.write(
                "| " + " | ".join(str(row[column]).replace("|", "\\|") for column in COLUMNS) + " |\n"
            )

    provenance = {
        "source_exchange": "Binance USDⓈ-M Futures",
        "production_market_data": True,
        "binance_testnet": False,
        "engine_commit": git_head(),
        "bridge_timestamp_epoch": snapshot_ts,
        "snapshot_age_seconds": round(age, 3),
        "bridge_rows": len(rows),
        "requested_rows": len(REQUESTED_SYMBOLS),
        "live_rows": sum(1 for row in output if row["Price"] != "UNAVAILABLE"),
        "unavailable_rows": sum(1 for row in output if row["Price"] == "UNAVAILABLE"),
        "quality_counts": quality_counts,
        "real_orders": False,
        "orders_policy": "NO_REAL_ORDERS",
        "generated_by": "canonical Python live-sheet exporter",
    }
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    print(json.dumps(provenance, indent=2))
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {md_path}")
    print(f"Wrote: {provenance_path}")


if __name__ == "__main__":
    main()
