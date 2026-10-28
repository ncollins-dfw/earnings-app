#!/usr/bin/env bash
# (Re)start the earnings app on port 8765 (logs: server.log).
# Optional: ./run.sh tunnel  -> also tries a Cloudflare quick tunnel (blocked on this box as of 2026-10-27).
cd "$(dirname "$0")"
pkill -f "earnings-app/server.py" 2>/dev/null; sleep 1
nohup /workspace/venv/bin/python /workspace/earnings-app/server.py > server.log 2>&1 &
sleep 2; curl -s -m 5 localhost:8765/healthz >/dev/null && echo "server up: http://localhost:8765" || echo "server failed; see server.log"
if [ "$1" = tunnel ]; then
  pkill -f "cloudflared tunnel --no-autoupdate" 2>/dev/null
  nohup cloudflared tunnel --no-autoupdate --url http://localhost:8765 > tunnel.log 2>&1 &
  sleep 20; grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunnel.log | head -1
  grep -q "Registered tunnel connection" tunnel.log && echo "tunnel connected" || echo "tunnel NOT connected (see tunnel.log)"
fi
