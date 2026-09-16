# Production Market-Data Contract

## Purpose

The live scanner may use an observation for signal or risk decisions only when the observation is traceable to an exchange source, carries an observation timestamp, and passes freshness checks.

## States

- `LIVE`: current exchange observation within its configured freshness window.
- `CALCULATED`: deterministic value derived only from valid upstream observations.
- `STALE`: a previously valid observation whose age exceeds its freshness limit.
- `UNAVAILABLE`: the required source did not return the metric.
- `NOT_APPLICABLE`: the metric has no event/value for the current market state.

Missing data must never be converted to zero for a directional decision.

## Binance market-data path

The public market-data WebSocket uses the production feed even when execution REST is configured for testnet. The effective scanner requires real `aggTrade`, `bookTicker`, `openInterest`, and `depth@100ms` per-symbol feeds. The environment contract checks the resulting stream budget before startup.

Ticker-24h observations are informational/selection data only. They are never emitted as synthetic trade events and therefore cannot create artificial delta or CVD.

## Derived data

Delta, CVD, B/S ratio, order-book imbalance, sweep detection, FVG structure, and regime are derived only from valid upstream observations. A derived value must preserve the lineage of those observations.

## Signal boundary

Only the Python engine is a canonical executable signal authority. The dashboard and Node API may present evidence and canonical signals but may not infer BUY/SELL by voting across displayed factors.

## Freshness

The dashboard suppresses current executable-signal display whenever its bridge market snapshot is not `LIVE`. A stale snapshot can remain visible for diagnosis, but it cannot masquerade as current trading evidence.
