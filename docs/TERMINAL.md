# Terminal build contracts (2026-09-20, the last hours)

One URL, `?view=terminal|map|tiger|eye`. Narrow screens default to `eye`. `App.tsx` routes; do not edit it.

## Files and owners (one worktree each; touch only your files)

| Owner | Files |
| --- | --- |
| eye | `mission-control/src/ScoutEye.tsx`, `EyeView.tsx`, `eye.css` |
| terminal | `mission-control/src/Terminal.tsx`, `terminal.css`, `Drive.tsx` (new), status/health/ticker/tape/rule/clock items inside Terminal.tsx |
| radar | `mission-control/src/Radar.tsx`, `MapTab.tsx`, `QrCard.tsx`, `radar.css`; may add the `qrcode` dependency to `package.json` |
| tiger-ui | `mission-control/src/TigerPanel.tsx`, `TigerTab.tsx`, `tiger.css` |
| tail | `tools/upload-run/live_tail.py`, `tools/upload-run/requirements.txt`, `tools/tunnel.sh`, `mission-control/vite.config.ts`, the default-URL change in `mission-control/src/scout.ts` (same-origin `/ws` when the page is not on localhost) |

Shared, read-only for everyone: `store.ts`, `protocol.ts`, `verdict.ts`, `scout.ts` (except the tail owner's one change), `SourceBar.tsx`, `Live.tsx`, `MapView.tsx`, `LidarView.tsx`, `History.tsx`.

## Data contracts

`data/live/tiger.json` (served at `/live/tiger.json`, rewritten every second by `live_tail.py`; absent = tailer not running):
```json
{"updated_at":"2026-09-20T05:10:00Z","db_ok":true,"error":"",
 "rows_total":2089080,"rows_session":51120,"rows_per_s":3600,
 "on_disk_bytes":4160000,"raw_bytes_est":117000000,"ratio":28.1,
 "last_second":{"frames":10,"min_clearance_mm":412,"empty_share":0.61},
 "events_total":93,"query":{"sql":"select count(*) from scan","ms":61},
 "aggregates":[{"name":"scan_1s","real_time":true,"rows":1482}],
 "direct_compress":true}
```
`data/live/tunnel.json` (written by `tools/tunnel.sh`): `{"url":"https://xxxx.trycloudflare.com","started_at":"..."}`.

`/tiger/query` (Vite proxies to `127.0.0.1:8787`, served by `live_tail.py`, read-only transaction, credential never leaves the process):
`POST {"sql": "..."}` or `GET /tiger/query?preset=<name>` -> `{"columns":[...],"rows":[[...]],"ms":12,"error":""}`. Presets: `rows`, `compression`, `door_history`, `aggregates`, `latest_verdicts`.

## Look
Dark, dense, default monospace (`ui-monospace, Menlo, Consolas, monospace`), no gradients, no rounded corners over 2 px, no animation except the drive keys lighting on press. Colours: bg `#0b0d10`, panel `#12151a`, rule `#262b33`, fg `#d7dde5`, dim `#8a939e`, amber `#e9b74f`, red `#e0443e`, green `#3ddc84`, blue `#4da3ff`. Every panel has a one-word uppercase label in dim. Numbers in the fg colour, big where they matter.

## Verification per worktree
`cd mission-control && npm install && npx tsc --noEmit && npm run build`. To see data: `PORT=8090 npm run fake` (repo root) and pick "Live: other address…" -> `ws://localhost:8090/ws`, or `?view=...` with the replay source. Commit on your branch with a clear message; do not push; do not touch main.

## Shared pieces added after the stubs (do not edit; import them)
- `mission-control/src/palette.ts`: `C` (the colours above) and `distanceColour(mm)` (red 400 → amber 860 → green 2000 → blue 5000, RGB blend). Every view colours distance with it.
- `mission-control/src/Tabs.tsx`: `<Tabs />`, the four-screen switcher. Put it in your screen's header.
- `App.tsx` sets `document.documentElement.dataset.view` to `terminal|map|tiger|eye`. Scope page-level styles as `html[data-view="terminal"] body { margin: 0; background: #0b0d10 }`. `style.css` stays Quaden's light theme for the Inferred position tab.
