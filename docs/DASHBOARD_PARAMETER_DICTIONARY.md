# Dashboard Parameter Dictionary

The dashboard has one canonical executable field: `Signal`. Every other field is evidence, context, or risk information.

| Parameter | BUY meaning | SELL meaning | Otherwise / role |
|---|---|---|---|
| Price | Positive price direction | Negative price direction | Flat = NEUTRAL |
| OI Bias | Engine identifies bullish positioning | Engine identifies bearish positioning | Unavailable = no vote |
| OI Δ% | Increasing OI interpreted with price/positioning | Decreasing/increasing OI interpreted with price/positioning | Raw change, not standalone |
| Funding | Funding state supports long-side positioning | Funding state supports short-side positioning | Positioning/risk modifier |
| Fund Bias | Engine's bullish funding interpretation | Engine's bearish funding interpretation | NEUTRAL when inconclusive |
| B/S Ratio | > 1.02 | < 0.98 | Between = NEUTRAL |
| Net Delta | Positive taker delta | Negative taker delta | Missing = UNAVAILABLE |
| Delta | BUY = positive pressure | SELL = negative pressure | NEUTRAL otherwise |
| CVD | Rising/positive CVD bias | Falling/negative CVD bias | Derived from actual trade tape |
| Flow | Actual flow classifier says BUY | Actual flow classifier says SELL | No estimate/fabrication |
| Ex Flow | Exchange-flow classifier says BUY | Exchange-flow classifier says SELL | Missing remains unavailable |
| Vol Bias | Bullish volume classification | Bearish volume classification | Context if neutral |
| Imbalance | > +0.05 | < -0.05 | Otherwise NEUTRAL |
| Liquidity Zone ↓ | Location/context below price | Location/context below price | Never an independent vote |
| Liquidity Zone ↑ | Location/context above price | Location/context above price | Never an independent vote |
| Liq Risk | Acceptable risk context for a BUY | Acceptable risk context for a SELL | Risk state, not a signal |
| Sweep | Qualifying bullish sweep/reclaim | Qualifying bearish sweep/rejection | No event = NOT_APPLICABLE |
| Sweep Price | Bullish event price | Bearish event price | Event location only |
| FVG | Bullish FVG alignment | Bearish FVG alignment | Calculated structure |
| FVG Price | Bullish gap location | Bearish gap location | Context only |
| Regime | Bullish regime | Bearish regime | Range/unknown = NEUTRAL |
| Reg Conf | Confidence in bullish regime | Confidence in bearish regime | Not standalone |
| Signal | **Canonical Python BUY** | **Canonical Python SELL** | `NO_SIGNAL` otherwise |

## Authenticity rule

A dashboard field that says BUY/SELL is a directional interpretation of that field only. It is not a trade instruction. A trade instruction exists only in the canonical Python `Signal` field.

## Missing-value rule

A blank source value is not equivalent to zero, neutral, BUY, or SELL. The rendering state must be explicit: `UNAVAILABLE`, `STALE`, or `NOT_APPLICABLE`.
