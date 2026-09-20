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
- **Link**: silent while it is working. 2 s without a frame, or a dropped socket, raises LINK DOWN over the map and greys the last values. Reconnects every second, forever. A missing device or a protocol mismatch is named in the header; the firmware and the address are not, `curl /status` has those.
- **The map** is the screen, drawn like a floor plan: the fitted room as a bold outline on white, every surface the lidar hit in ink, an obstacle seen all the way round as a solid block, the path Scout drove as a dashed line, Scout as the logo with an arc ahead of it for how far the way is clear, and a marker for every obstacle, mark and width verdict. An obstacle marker carries no name: Scout has no camera, so the lidar places it and that is all. Room dimensions sit in the corner; NO POSE and NO LIDAR appear there when they apply. Plain 2D canvas, no libraries.
- **Clearance**: the gap Scout is passing through right now against the 860 mm limit. "no gap" means there is nothing to measure across, which is the normal reading in open space.
- **Controls**, left to right in the order a run happens: the space name, START RUN, MARK, DOWNLOAD, CLEAR MAP.
  - **Start run** tells Scout to clear its map and run buffer and read the room frame afresh, and starts recording frames in the browser. The tag beside the button reads FINDING POSITION… and then POSITION LOCKED, or the room size when the rectangle fitter is the pose engine (`SCOUT_POSE=rect`). That size is the moment to check against the tape measure: the fitter reads the room off its first clean scan, so a wall hidden behind furniture at that moment locks in a wrong room for the whole run. NO POSITION after 5 s means move Scout and start again.
  - **Stop run** also stops the motors, so a run never ends with Scout still driving, and saves the recording as an NDJSON replay file (one map frame per 10 s, so replays fill the room in).
  - **Download** hands out the run four ways. **Run report (.html)** is the one a person reads: one self contained page with the map drawn into it, the width verdicts, the obstacles and where they are, and what the numbers rest on. It opens in Chrome and prints to PDF. Behind it are the tables: events and telemetry (without the scans) as CSV, and the map as a table of cells (`x_mm,y_mm,state`) or as the PNG on screen. Everything since run start, or since connecting when no run was started.
  - **Clear map** wipes the board. Scout drops its map, its room frame and its audit together, so the dashboard drops the trail, every pin and the tables behind Download with them: nothing is left on screen that Scout has forgotten, and the same chair is reported again as it finds it. **Start run** does the same thing.
  - A page load or a source switch clears Scout's map, unless Scout reports a run in progress: on the robot a map clear also drops the room frame, so a mid-run reload leaves it alone and shows the run still going.
- **Drive**: WASD or arrows, resent every 100 ms while held, `stop` on release. **Space is the E-STOP** and works anywhere on the page, which is why the pad has no stop button. Losing window focus sends `stop`. Driving cancels wall following, which the Pi still accepts (`/cmd?c=roam`) but the dashboard no longer offers.
- **Voice**: verdicts are spoken with the browser's `speechSynthesis`. Click the page once before the demo (Chrome may block speech before the first interaction). The checkbox mutes it. Width verdicts and marks are spoken; obstacles are not, because there are too many of them.

`data/` at the repo root is served at `/` (Vite `publicDir`).
