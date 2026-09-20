# Scout

Scout is a small rover that is driven around a room by hand and draws a 2D accessibility map of it as it goes. The lidar is its only sense: walls, obstacles, gap widths. There is no camera, so an obstacle is placed on the map and never named. Gaps are compared to the Ontario Building Code's 860 mm clear width and failures get a spoken verdict in the browser. A browser dashboard drives it, shows the live map and replays recorded runs.

**Scout is driven by hand, not autonomously.** A human drives from the laptop with the arrow keys or WASD, on the same screen as the map. `wall_follow` still exists in the protocol, in `pi/scout/wallfollow.py` and in the recorded run, and the Pi still accepts it, but it is no longer being developed and has no button on the dashboard. Do not build new work on it.

**Scout has no IMU and measures no slope.** A ramp meeting a horizontal scan plane looks exactly like a wall, and nothing on Scout tells the two apart. Clearance width is the only building-code verdict.

Hack the North 2026. Submission closes Sunday 2026-09-20 08:00 EDT. Full spec: `docs/SPEC.md`. Contract: `docs/PROTOCOL.md`. Everything cut: `docs/LATER.md`.

## Architecture

```
RedBoard (motors, 600 ms watchdog)  <-- USB serial -->  Pi 4 (RPLIDAR A2M8, pose, map, audit, protocol server :8080)  <-- wifi -->  dashboard (Chrome)
```

There is no Hub. The dashboard talks to the Pi directly. The fake Scout and any replay file are interchangeable with the real robot. Development happens on the Mac with the motor board and the lidar plugged in by USB; the Pi service is copied to the Pi unchanged.

Position has no sensor behind it: there is no odometry and no IMU. There are two ways to get it and `SCOUT_POSE` picks one:

- **`slam` (the default)** — `pi/scout/slam.py` matches each scan against the map built so far, the way Hector SLAM does. Works in any shape of space, and **it drifts**: there is no loop closure, so error accumulates over a run. Reports `room: null`, and the map is drawn on a fixed canvas with the starting point at its centre. Needs numpy; without it the service falls back to `rect` and says so loudly.
- **`rect`** — `pi/scout/pose.py` fits the rectangle of the room in every scan and reads position and heading off it. Cannot drift, but needs one closed rectangular room and nothing else.

Measured on `data/runs/room-scan.ndjson` (606 scans, ground truth in every frame): slam holds pose on 99% of scans at 17 mm median error and ends the run 17 mm out. The rect fit manages 2.0 mm median in the rooms it can handle. **Quote the drift, not the 2 mm, whenever slam is the engine.**

## Workstreams and owners

| Workstream | Owner | Folder | Delivers |
| --- | --- | --- | --- |
| BODY (hardware) | `<name>` | `hardware/` | Chassis, wiring per `hardware/PINMAP.md`, motor driver and 5 V rail (both still open), lidar mount, the test room |
| BRAIN, firmware (RedBoard) | Aidan | `firmware/` | Drive and watchdog over serial. No beep and no LEDs: the board has neither |
| BRAIN, Pi service | `<name>` | `pi/` | Serial to the motor board, lidar, pose, occupancy map, clearance, wall following, `/status` `/cmd` `/ws` `/map` `/runs/latest` (`/photo` is in the protocol and always 404: there is no camera) |
| MISSION CONTROL (dashboard) | Aikhan | `mission-control/` | Live 2D map, teleop with E-STOP, browser TTS verdicts, replay |
| Coordinator (no code) | `<name>` | `data/` | Test room, tape-measure ground truth, `rules.json`, pitch, Devpost |

Replace each `<name>` with the real owner.

## Run commands

```
# fake Scout: protocol v2 on :8080, a live simulated robot that obeys every command (drive, stop, mode, run, mark, map clear)
npm run fake                                   # (root) or: cd tools/fake-scout && npm install && npm start
node tools/fake-scout/server.js data/runs/room-scan.ndjson   # play a recorded run on a loop instead (commands are logged, not obeyed)
npm run gen                                    # regenerate the sample run from the same simulator (tools/fake-scout/sim.js)

# dashboard: Vite on :5173, serves data/ as static files
npm run dash                                   # or: cd mission-control && npm install && npm run dev

# Pi service (also runs on the Mac). Finds the motor board and lidar by probing USB serial ports.
cd pi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scout                      # :8080. SCOUT_MOTOR_PORT / SCOUT_LIDAR_PORT override discovery ("none" disables)
.venv/bin/python tools/fake_redboard.py        # a fake RedBoard on a pty, for testing without hardware
.venv/bin/python tools/check_slam.py           # replay the recorded run through slam, scored against its ground truth
SCOUT_POSE=rect .venv/bin/python -m scout      # the rectangle fitter instead of slam

# motor firmware: 06_serial_drive.ino on the RedBoard, flashed from the Arduino IDE (owner: Aidan).
# firmware/ still holds the ESP32 bridge that was never used -- see firmware/README.md.

# Tiger Data (sponsor track): laptop-only, after a run. Needs TIGER_URL in the repo-root .env (gitignored, never printed)
npm run setup:tiger                            # once
npm run upload -- upload data/runs/<run>.ndjson   # hypertables + columnstore, prints the compression ratio, refreshes data/history.json
npm run upload -- status

# data
data/runs/*.ndjson      run logs and replay files (runs/index.json lists them for the dashboard)
data/rules.json         the width limit, its rule text and source
```

## Protocol v2 in ten lines (full text in docs/PROTOCOL.md)

- Scout is the Pi: `scout.local:8080`, HTTP + WS `/ws`, CORS `*`. `proto` is now `2`; v1 consumers will not interoperate.
- `GET /status` (with `devices`), `POST /cmd`, `GET /cmd?c=forward|back|left|right|stop|beep|roam`, `GET /map`, `GET /photo/<id>`, `GET /runs/latest` (NDJSON).
- Commands: `drive v w`, `stop`, `mode idle|teleop|wall_follow`, `run start|stop`, `mark`, `map clear`, `beep`, `config`. Also accepted as text frames on `/ws`.
- Teleop watchdog on the motor board: no `drive` for 600 ms stops the motors. The dashboard resends every 100 ms while a key is held. `drive` cancels `wall_follow`.
- `telem` at 10 Hz: `t mode measuring lidar scan[360] pose x_mm y_mm heading_deg room clearance_mm bump stuck v w`.
- `scan` is exactly 360 ints, index = bearing in degrees counter-clockwise of straight ahead (index 270 is the right side), 0 = no return.
- `gaps` are openings found in that scan, each with `evidence` of `see_through|step|unverified`. An `unverified` gap is an arc of no-returns, where a doorway and a non-reflective wall look identical, and must never be presented as a measurement.
- `pose` false means no position: `x_mm`/`y_mm`/`heading_deg` are 0 and mean nothing. **Honour the flag** — a confident wrong pose corrupts the map for the rest of the run.
- `map` frame at 1 Hz: an occupancy grid in the room frame, `cells` a string of `0` unknown / `1` free / `2` occupied. It goes over `/ws` so replay files carry the map.
- `event`: kinds `obstacle` (`label` is always `unknown`, `confidence` 0, `photo` empty: there is no camera), `width_pass|fail` (with `between`), `mark`, `run_start|stop`. `ramp` is in the protocol and never emitted. Only width events are verdicts; obstacles are observations. The beep and the light went with the ESP32, so a verdict is spoken and shown only.
- Pi to motor board (115200): a SparkFun RedBoard. `pi/scout/redboard.py` translates `D v w` / `S` into its `<L> <R>` / `s` dialect. `B` and `L` are discarded: no buzzer, no LEDs.

## Rules

1. No scope creep. New ideas go in `docs/LATER.md`, not in code. The coordinator says no.
2. `docs/PROTOCOL.md` is frozen. A change needs sign-off from all three code owners, and the doc changes before the code.
3. All P0 across all workstreams before anyone starts P1. `docs/SPEC.md` has the cut order.
4. Commit small and often. `main` must always run. Never commit `node_modules`, `.venv`, `.pio` or `.env`.
5. Build against the fake Scout and the fake RedBoard first. Nobody waits for hardware.
6. Boring technology only. No new frameworks, no database, no cloud. Everything runs on one laptop and one Pi on a phone hotspot at 2.4 GHz.
7. Real numbers only, on screen and in the pitch, with the measured error stated. Scout has no camera and no IMU: it never names an obstacle and never judges a ramp. Say so.
8. Scout has no cliff sensor, no odometry and no IMU. Never run it near stairs or a drop. Position comes from fitting the room's rectangle, so it exists only inside one rectangular room and vanishes without warning; every consumer must handle `pose:false`.
9. The Pi service must run with the lidar absent, the motor board absent, or both. A missing device disables its own feature only and is logged loudly at startup. Serial ports are found by probing, never by `/dev/ttyUSB0`.
10. A milestone is done when a human has run its acceptance test on the real laptop or the real robot. When two approaches work, pick the one a tired human can debug at hour 20.

## Claude Code notes

- Read `docs/PROTOCOL.md` before touching code that sends or receives frames or serial lines. Match the keys exactly.
- Pi service: Python 3.9+, pyserial + aiohttp, and numpy for `slam.py` only (without it the service falls back to `SCOUT_POSE=rect`). `pi/scout/rplidar.py` is vendored from `~/dev/lidar-gaps` (not a git repo); edit there first. Threads for the two serial devices, asyncio for the server, immutable snapshots instead of locks.
- `pose.py` and `mapping.py` are the load-bearing algorithms and both have real failure modes documented in their docstrings. Test changes against a recorded run before the robot: `data/runs/room-scan.ndjson` carries 606 scans with ground-truth poses in every frame.
- Firmware: the board is a SparkFun RedBoard (ATmega328P) running `06_serial_drive.ino`, owned by the firmware workstream and flashed from the Arduino IDE. It drives the shield's SN74HC595 directly with no library; do not reintroduce one. `firmware/` still holds the unused ESP32 bridge. The Pi never sees the difference: `pi/scout/redboard.py` is the only file that knows the board's dialect.
- Dashboard: Vite + React + TypeScript, `zustand`, and a plain 2D canvas for the map. No three.js, no GLB. Chrome only. Vite `publicDir` is `../data`.
- Tiger Data: only `tools/upload-run` touches the database, never the Pi service or the dashboard. The dashboard reads `data/history.json`. Simulated runs (fw `fake*`) are refused unless `--simulated` and are tagged so they never show by default.
- Keep code minimal and plain. One file that reads top to bottom beats a clever abstraction.
