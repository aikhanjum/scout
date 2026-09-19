# Scout

Scout is a small rover that drives itself around a room like a Roomba and draws a 2D accessibility map of it. The lidar does geometry (walls, obstacles, gap widths); the camera does naming (what each obstacle is, including whether it is a ramp). Gaps are compared to the Ontario Building Code's 860 mm clear width and failures get a light, a beep and a spoken verdict. A browser dashboard drives it, shows the live map and replays recorded runs.

**Scout has no IMU and measures no slope.** A ramp meeting a horizontal scan plane looks exactly like a wall, so ramps are labelled by the camera and never judged. Clearance width is the only building-code verdict.

Hack the North 2026. Submission closes Sunday 2026-09-20 08:00 EDT. Full spec: `docs/SPEC.md`. Contract: `docs/PROTOCOL.md`. Everything cut: `docs/LATER.md`.

## Architecture

```
ESP32 (motors, 500 ms watchdog)  <-- USB serial -->  Pi 4 (RPLIDAR A1, camera, pose, map, audit, protocol server :8080)  <-- wifi -->  dashboard (Chrome)
```

There is no Hub. The dashboard talks to the Pi directly. The fake Scout and any replay file are interchangeable with the real robot. Development happens on the Mac with the ESP32 and the lidar plugged in by USB; the Pi service is copied to the Pi unchanged.

Position has no sensor behind it: there is no odometry and no IMU. `pi/scout/pose.py` fits the rectangle of the room in every scan and reads position and heading off it, which is why the test space must be one rectangular room.

## Workstreams and owners

| Workstream | Owner | Folder | Delivers |
| --- | --- | --- | --- |
| BODY (hardware) | `<name>` | `hardware/` | Chassis, wiring per `hardware/PINMAP.md`, motor driver and 5 V rail (both still open), lidar and camera mounts, the test room |
| BRAIN, firmware (ESP32 bridge) | `<name>` | `firmware/` | Drive, watchdog, beep, LEDs over serial |
| BRAIN, Pi service | `<name>` | `pi/` | Serial to the ESP32, lidar, pose, occupancy map, clearance, wall following, camera labels, `/status` `/cmd` `/ws` `/map` `/photo` `/runs/latest` |
| MISSION CONTROL (dashboard) | Aikhan | `mission-control/` | Live 2D map, teleop with E-STOP, browser TTS verdicts, replay |
| Coordinator (no code) | `<name>` | `data/` | Test room, tape-measure ground truth, `rules.json` labels, pitch, Devpost |

Replace each `<name>` with the real owner.

## Run commands

```
# fake Scout: protocol v2 on :8080, loops data/runs/room-scan.ndjson, logs every command
npm run fake                                   # (root) or: cd tools/fake-scout && npm install && npm start
npm run gen                                    # regenerate the sample run (a little 2D room simulator)

# dashboard: Vite on :5173, serves data/ as static files
npm run dash                                   # or: cd mission-control && npm install && npm run dev

# Pi service (also runs on the Mac). Finds the ESP32 and lidar by probing USB serial ports.
cd pi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scout                      # :8080. SCOUT_ESP32_PORT / SCOUT_LIDAR_PORT override discovery ("none" disables)
.venv/bin/python tools/fake_esp32.py           # a fake ESP32 on a pty, for testing without hardware
python pi/tools/make_clip_labels.py             # on a LAPTOP: build pi/models/ for camera labels

# ESP32 bridge firmware: PlatformIO, board esp32dev (pipx install platformio; pio is in ~/.local/bin)
npm run flash                                  # or: cd firmware && pio run -t upload && pio device monitor -b 115200
cd firmware && pio run                         # compile only

# data
data/runs/*.ndjson      run logs and replay files (runs/index.json lists them for the dashboard)
data/rules.json         the width limit, its rule text and source, and the camera's label set
```

## Protocol v2 in ten lines (full text in docs/PROTOCOL.md)

- Scout is the Pi: `scout.local:8080`, HTTP + WS `/ws`, CORS `*`. `proto` is now `2`; v1 consumers will not interoperate.
- `GET /status` (with `devices`), `POST /cmd`, `GET /cmd?c=forward|back|left|right|stop|beep|roam`, `GET /map`, `GET /photo/<id>`, `GET /runs/latest` (NDJSON).
- Commands: `drive v w`, `stop`, `mode idle|teleop|wall_follow`, `run start|stop`, `mark`, `map clear`, `beep`, `config`. Also accepted as text frames on `/ws`.
- Teleop watchdog on the ESP32: no `drive` for 500 ms stops the motors. The dashboard resends every 100 ms while a key is held. `drive` cancels `wall_follow`.
- `telem` at 10 Hz: `t mode measuring lidar scan[360] pose x_mm y_mm heading_deg room clearance_mm bump stuck v w`.
- `scan` is exactly 360 ints, index = bearing in degrees counter-clockwise of straight ahead (index 270 is the right side), 0 = no return.
- `gaps` are openings found in that scan, each with `evidence` of `see_through|step|unverified`. An `unverified` gap is an arc of no-returns, where a doorway and a non-reflective wall look identical, and must never be presented as a measurement.
- `pose` false means no position: `x_mm`/`y_mm`/`heading_deg` are 0 and mean nothing. **Honour the flag** — a confident wrong pose corrupts the map for the rest of the run.
- `map` frame at 1 Hz: an occupancy grid in the room frame, `cells` a string of `0` unknown / `1` free / `2` occupied. It goes over `/ws` so replay files carry the map.
- `event`: kinds `obstacle`, `ramp` (both with `label`, `confidence`, `photo`), `width_pass|fail` (with `between`), `mark`, `run_start|stop`. Only width events beep and light up; obstacles are observations, not verdicts.
- Pi to ESP32 serial (115200): text in (`D v w`, `S`, `B p`, `L r g`, `T`), JSON lines out at 10 Hz. No IMU fields.

## Rules

1. No scope creep. New ideas go in `docs/LATER.md`, not in code. The coordinator says no.
2. `docs/PROTOCOL.md` is frozen. A change needs sign-off from all three code owners, and the doc changes before the code.
3. All P0 across all workstreams before anyone starts P1. `docs/SPEC.md` has the cut order.
4. Commit small and often. `main` must always run. Never commit `node_modules`, `.venv`, `.pio` or `.env`.
5. Build against the fake Scout and the fake ESP32 first. Nobody waits for hardware.
6. Boring technology only. No new frameworks, no database, no cloud. Everything runs on one laptop and one Pi on a phone hotspot at 2.4 GHz.
7. Real numbers only, on screen and in the pitch, with the measured error stated. Ramps are labelled by the camera, never judged, because nothing on Scout measures slope. Say so.
8. Scout has no cliff sensor, no odometry and no IMU. Never run it near stairs or a drop. Position comes from fitting the room's rectangle, so it exists only inside one rectangular room and vanishes without warning; every consumer must handle `pose:false`.
9. The Pi service must run with the lidar absent, the ESP32 absent, the camera absent, or all three. A missing device disables its own feature only and is logged loudly at startup. Serial ports are found by probing, never by `/dev/ttyUSB0`.
10. A milestone is done when a human has run its acceptance test on the real laptop or the real robot. When two approaches work, pick the one a tired human can debug at hour 20.

## Claude Code notes

- Read `docs/PROTOCOL.md` before touching code that sends or receives frames or serial lines. Match the keys exactly.
- Pi service: Python 3.9+, pyserial + aiohttp only. Camera labelling additionally wants `onnxruntime`, `numpy`, `Pillow` and `picamera2`, and degrades to "unknown" without any of them. `pi/scout/rplidar.py` is vendored from `~/dev/lidar-gaps` (not a git repo); edit there first. Threads for the two serial devices, asyncio for the server, immutable snapshots instead of locks.
- `pose.py` and `mapping.py` are the load-bearing algorithms and both have real failure modes documented in their docstrings. Test changes against a recorded run before the robot: `data/runs/room-scan.ndjson` carries 660 scans with ground-truth poses in every frame.
- Firmware: `platform = espressif32@^6.9` (Arduino core 2.0.x, `ledcSetup`/`ledcAttachPin`), no libraries. Nothing in `loop()` blocks except the `T` self-test. Pins and sign flips live in `firmware/src/config.h` and mirror `hardware/PINMAP.md`.
- Dashboard: Vite + React + TypeScript, `zustand`, and a plain 2D canvas for the map. No three.js, no GLB. Chrome only. Vite `publicDir` is `../data`.
- Keep code minimal and plain. One file that reads top to bottom beats a clever abstraction.
