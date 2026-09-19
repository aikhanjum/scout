# mission-control

The dashboard. Vite + React + TypeScript. Chrome only. Talks to Scout directly (`docs/PROTOCOL.md`).

## Run

```
cd mission-control
npm install
npm run dev        # http://localhost:5173
npm run check      # typecheck
```

Or from the repo root: `npm run dash`. Start `npm run fake` (root) first if there is no robot.

## What it does

- **Source bar** (always visible): connect to a live Scout by URL (`ws://localhost:8080/ws` is the fake, `ws://scout.local/ws` is the robot), or play a replay file from `data/runs/` or from disk. Swap mid-demo.
- **Link**: LINK UP only while telemetry arrives. 2 s without a frame, or a dropped socket, shows LINK DOWN and greys the last values. Reconnects every second, forever.
- **LIVE**: slope gauge (limit line, MEASURING tag), width bar (limit line, full-scale line in TABLE MODE), event feed with verdict captions, drive pad, run/mark/zero/beep, REC to save the incoming frames as an NDJSON replay file.
- **Drive**: WASD or arrows, resent every 100 ms while held, `stop` on release. Space bar is E-STOP. Losing window focus sends `stop`.
- **Voice**: verdicts are spoken with the browser's `speechSynthesis`. Click the page once before the demo (Chrome may block speech before the first interaction). The checkbox mutes it.
- **TABLE MODE**: sends `config scale 0.25` and shows the "Scale course 1:4" badge. Never presents course numbers as building numbers.
- **SPACES**: summary banner and checkpoints from `data/spaces.json`, pass/fail from `data/rules.json`. The 3D viewer with pins is the next increment on this screen.

`data/` at the repo root is served at `/` (Vite `publicDir`).
