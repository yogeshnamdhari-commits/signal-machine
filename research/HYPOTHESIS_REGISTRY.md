# Order-Flow Hypothesis Registry

## Current preregistration

| ID | Status | Instrument | Scope | Registered branch |
|---|---|---|---|---|
| ORDERFLOW_STATE_TRANSITION-0.1 | PREREGISTERED — DATA COLLECTION NOT RUN | BTCUSDT USD-M perpetual | Multi-level OFI state-transition + aggressive trade-flow confirmation | `research/orderflow-state-transition-01` |

### Governance
- The hypothesis is registered before any new capture is inspected for this hypothesis.
- No live order submission is permitted.
- Existing frozen/rejected candidates remain immutable.
- Results that fail the economic gate are closed/rejected; parameters are not silently retuned.
- The canonical executable signal/risk authority remains the existing Python engine.

## Registration basis

The hypothesis targets a different object from a static order-book imbalance signal: a **transition into a persistent, aligned multi-level order-flow state**. The research question is whether the *change into* an aligned state contains information beyond a single contemporaneous imbalance snapshot.

## Economic gate

Use the established BTCUSDT taker reference:
- Round-trip transaction cost: 3.4 bps
- Safety buffer: 2.0 bps
- Minimum gross edge required: **5.4 bps**
- Net edge: gross edge minus 3.4 bps

Primary economic decision is made on pooled untouched OOS data. Per-session results must also be reported.

## References

- Binance USD-M market-stream documentation: current public market-data streams include `aggTrade` and `depth`. 
- Cont, Kukanov & Stoikov: order-flow imbalance is related to short-horizon price changes and depth.
- Xu, Gould & Howison: multi-level order-flow imbalance contains information beyond the best quote level in their empirical study.

See `research/ORDERFLOW_STATE_TRANSITION_01_SPEC.md` for the complete fixed protocol.
