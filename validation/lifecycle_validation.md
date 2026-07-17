# Lifecycle Validation Log

## Post-Fix Trade Validation

| # | Symbol | Side | Entry | MFE | MAE | Hold | Exit | Exit Reason | PnL | Status |
|---|--------|------|-------|-----|-----|------|------|-------------|-----|--------|
| — | (waiting for first post-restart trade) | | | | | | | | | |

## Validation Checklist

For each trade, verify:
- [ ] Entry price correct
- [ ] Entry timestamp correct
- [ ] MFE updates while open
- [ ] MAE updates while open
- [ ] Hold duration increasing
- [ ] Exit trigger correct
- [ ] Exit price recorded
- [ ] Exit reason recorded
- [ ] Final PnL correct
- [ ] Persisted to analytics

## Failures
(none yet)
