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

- **Source bar** (always visible): connect to a live Scout by URL (`ws://localhost:8080/ws` is the fake, `ws://scout.local:8080/ws` is the robot), or play a replay file from `data/runs/` or from disk. Swap mid-demo.
- **Link**: LINK UP only while telemetry arrives. 2 s without a frame, or a dropped socket, shows LINK DOWN and greys the last values. Reconnects every second, forever.
- **The map** is the screen: the occupancy grid, the live scan drawn through Scout's current pose, Scout itself as a triangle (amber while it is stopped looking at something), and a labelled marker for every obstacle, ramp, mark and width verdict. Room dimensions sit in the corner; NO POSE and NO LIDAR appear there when they apply. Plain 2D canvas, no libraries.
- **Clearance**: the gap Scout is passing through right now against the 860 mm limit. `--` means there is nothing to report, which is the normal reading in open space.
- **Controls**: ROAM and IDLE, run start/stop, mark, clear map, beep, and REC to save the incoming frames as an NDJSON replay file (one map frame per 10 s, so replays fill the room in).
- **Drive**: WASD or arrows, resent every 100 ms while held, `stop` on release. Space bar is E-STOP. Losing window focus sends `stop`. Driving cancels ROAM.
- **Voice**: verdicts are spoken with the browser's `speechSynthesis`. Click the page once before the demo (Chrome may block speech before the first interaction). The checkbox mutes it. Ramps and width verdicts are spoken; plain obstacles are not, because there are too many of them.

`data/` at the repo root is served at `/` (Vite `publicDir`).
