# ORDERFLOW_STATE_TRANSITION-0.1 — Capture Runbook

## Purpose

This runbook executes only the preregistered market-data capture. It does not place orders and does not evaluate or retune the hypothesis.

## Environment

From the repository root:

```bash
cd /Users/targetmobile/Downloads/binance_orderflow_autotrader_v2
python3 -m pip install -r packages/ai-engine/requirements.txt
```

The collector uses the production Binance USD-M public market-data feed and `GET /fapi/v1/depth` for the initial local-book snapshot. The three required public streams are BTCUSDT `depth@100ms`, `aggTrade`, and `markPrice@1s`.

## Capture order

Run **Capture 1** first:

```bash
python3 research/collect_orderflow_state_transition.py --capture 1
```

Inspect only for pipeline/data-integrity correctness. Do not use Capture 1 to select or tune the registered rule.

After Capture 1 is accepted as technically valid, run **Capture 2**:

```bash
python3 research/collect_orderflow_state_transition.py --capture 2
```

Then run **Capture 3**:

```bash
python3 research/collect_orderflow_state_transition.py --capture 3
```

Do not inspect Captures 2 or 3 for parameter selection before the preregistered evaluation is run.

## Expected output

Each session creates:

```
research/captures/ORDERFLOW_STATE_TRANSITION_01/capture_N/
  events.jsonl
  book_snapshots.jsonl
  manifest.json
```

The manifest records:
- capture role
- exchange/feed provenance
- event counts
- sequence/bridge integrity
- file SHA-256 fingerprints
- fixed economic hurdle
- explicit `capture_valid`
- `live_order_submission: false`
- `deployment: NO_DEPLOY`

## Fail-closed conditions

Do not score a session when:
- the depth bridge fails
- any depth sequence gap occurs
- a reconnect occurs
- required feeds are missing
- malformed events occur
- the reconstructed top-10 book becomes invalid

No synthetic data or guessed values may be inserted to repair a failed capture.

## Important

The collector does not submit orders, authenticate a trading account, or alter the live execution configuration. It is research-data acquisition only.

Current deployment state remains:

```
ECONOMIC_CERTIFICATION = NOT_CERTIFIED
LIVE_AUTHORIZATION      = BLOCKED
DEPLOYMENT              = NO_DEPLOY
```
