#!/bin/sh
# tools/tunnel.sh [port]   a public https URL for the dashboard on that port (default 5173).
#
# Runs a cloudflared quick tunnel in the foreground, writes data/live/tunnel.json
# ({"url":"https://....trycloudflare.com","started_at":"..."}) for the QR card, prints the URL,
# and removes the file and the tunnel when it stops. Vite proxies /ws, /status, /cmd, /map,
# /photo, /runs/latest and /tiger (mission-control/vite.config.ts), so through this one URL a
# phone or a judge reaches the dashboard, Scout and the Tiger tailer.
set -u
PORT="${1:-5173}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/data/live/tunnel.json"
LOG="${TMPDIR:-/tmp}/scout-tunnel.$$.log"

command -v cloudflared >/dev/null 2>&1 || brew install cloudflared || { echo "cloudflared missing and brew install failed"; exit 1; }
mkdir -p "$(dirname "$OUT")"

cloudflared tunnel --url "http://localhost:$PORT" >"$LOG" 2>&1 &
PID=$!
cleanup() { rm -f "$OUT" "$LOG"; kill "$PID" 2>/dev/null; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

URL=""
i=0
while [ -z "$URL" ] && [ "$i" -lt 60 ]; do
  sleep 1
  i=$((i + 1))
  kill -0 "$PID" 2>/dev/null || { cat "$LOG"; echo "cloudflared exited"; exit 1; }
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -n 1)
done
[ -n "$URL" ] || { cat "$LOG"; echo "no tunnel URL after 60 s"; exit 1; }

printf '{"url":"%s","started_at":"%s"}\n' "$URL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$OUT"
echo "$URL"
echo "(tunnel.json written; Ctrl-C stops the tunnel and removes it)"
wait "$PID"
