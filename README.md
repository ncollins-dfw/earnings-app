# Earnings reaction app (prototype)
- server.py: stdlib HTTP server + yfinance (uses /workspace/venv). GET /api/earnings?ticker=XYZ[&infer=1], / (index.html), /healthz
- Logic = nok.py (before-open: day0 = release-day session, base = prior close) / femy.py (after-close: day0 = next session, base = release-day close).
  Window = release-15d .. day0+30d, day_offset = calendar days from day0. Timing from Yahoo's earnings timestamp (ET):
  <09:30 before open, >=16:00 after close, in-between = during market (treated like before open), 00:00 = unknown -> inferred
  (bigger |move| of release-day session vs next session), flagged timing_inferred=true. infer=1 forces inference (testing).
- Cache: 30 min per ticker (2 min for not-found). Errors: 400 bad_ticker, 404 no_earnings/no_prices, 502 yahoo_error, 503 rate limit.
- Restart: /workspace/earnings-app/run.sh   (port 8765, log server.log)

## Change 2026-10-27: +N moves use TRADING days
- Summary fields are now d1_*, d5_*, d21_* = close 1, 5 and 21 trading sessions after day 0 (21 trading days ~ 1 month; replaces the calendar-day +30). JSON includes horizon_unit="trading_days"; table headers read "+1 td / +5 td / +21 td".
- This removes the Friday quirk where +1 equaled day 0 (calendar day +1 = Saturday).
- Intentional difference: these +N numbers will NOT match Gary's nokia_NOK_earnings_summary.csv / femy_earnings_summary.csv (calendar-day based). Day 0, base close and every window row (chart data, x-axis = calendar days) are unchanged and still match the window CSVs exactly.
- Deployment: see DEPLOY.md (requirements.txt, render.yaml, Procfile; start = `python server.py`, reads $PORT).
