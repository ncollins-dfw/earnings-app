# Deploying (Render free tier, or any host that sets $PORT)
The server is stdlib Python + yfinance. It binds 0.0.0.0 and reads the PORT env var (default 8765), so no code changes are needed.

Files: requirements.txt (pinned deps), render.yaml (Render blueprint), Procfile (Heroku/Railway-style), .python-version.

## Render (needs a Render account + a GitHub/GitLab repo; owner does this)
1. Push the contents of this folder (server.py, index.html, requirements.txt, render.yaml, Procfile, .python-version) to a repo.
2. Render dashboard > New > Blueprint > pick the repo (uses render.yaml), or New > Web Service with:
   - Runtime: Python 3 - Build: `pip install -r requirements.txt` - Start: `python server.py`
   - Health check path: `/healthz` - Instance type: Free
3. Open the https://<name>.onrender.com URL on the phone; "Add to Home screen" for an app icon.

## Notes
- Free Render services sleep after ~15 min idle; the first request then takes ~30-60 s to wake up.
- Cache is in memory, so it resets on every restart/sleep.
- Yahoo sometimes rate-limits or blocks cloud/datacenter IPs. If tickers fail with "yahoo_error"/"yahoo_rate_limited" on the host but work locally, that's why.
- No secrets or API keys are needed.

## Local
`pip install -r requirements.txt && PORT=8765 python server.py`, or `./run.sh` on the box.
