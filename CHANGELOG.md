# EMA V5 Changelog

## v5.0-stabilization — 2026-07-17

### Fixes
| Commit | Date | Issue | Impact | Validated |
|--------|------|-------|--------|-----------|
| `347a266` | 2026-07-17 | Dashboard contamination: 451 phantom forward_test trades inflating KPIs | Trade counts wrong (771→349), WR wrong (13.9%→32.7%) | ✅ Verified |
| `73cca75` | 2026-07-17 | Position monitoring gap: _price() returning None for open positions | 6 trades had zero MFE/MAE tracking, killed by time exit | ✅ Committed, needs runtime verification |
| `420ebdd` | 2026-07-17 | .env file with API keys in git history | Security risk | ✅ Purged from history |
| `b4e1821` | 2026-07-17 | Runtime data files tracked by git | Dirty working tree | ✅ Fixed |

### Architecture
- Architecture frozen during stabilization
- Production acceptance criteria defined in `docs/PRODUCTION_ACCEPTANCE.md`
- Engineering backlog in `memories/repo/engineering-backlog-frozen.md`

### Status
- [x] Monitoring fix committed
- [ ] Smoke test (first 5-10 trades)
- [ ] Code freeze
- [ ] Collect clean data
- [ ] Evaluate strategy on new trades
