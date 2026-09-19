# Scout

Scout is a small rover that audits indoor spaces for wheelchair accessibility. It measures floor slope (IMU) and gap width (lidar), compares them to Ontario Building Code limits, and calls out failures on the spot with a light, a beep and a spoken verdict. A browser dashboard drives it, shows the live readings, and pins every barrier onto a lidar 3D model of the space captured by an iPhone riding on Scout.

Hack the North 2026. Submission closes Sunday 2026-09-20 08:00 EDT. Full spec: `docs/SPEC.md`. Contract: `docs/PROTOCOL.md`. Everything cut: `docs/LATER.md`.

## Architecture

```
ESP32 (motors, IMU, 500 ms watchdog)  <-- USB serial -->  Pi 4 (RPLIDAR A1, audit logic, protocol server :8080)  <-- wifi -->  dashboard (Chrome)
```

There is no Hub. The dashboard talks to the Pi directly. The fake Scout and any replay file are interchangeable with the real robot. Development happens on the Mac with the ESP32 and the lidar plugged in by USB; the Pi service is copied to the Pi unchanged.

## Workstreams and owners

| Workstream | Owner | Folder | Delivers |
| --- | --- | --- | --- |
| BODY (hardware) | `<name>` | `hardware/` | Chassis, wiring per `hardware/PINMAP.md`, power for Pi + lidar + motors, the 1:4 table course |
| BRAIN, firmware (ESP32 bridge) | `<name>` | `firmware/` | Drive, IMU, watchdog, beep, LEDs over serial |
| BRAIN, Pi service | `<name>` | `pi/` | Serial to the ESP32, lidar, slope stop-and-measure, width pinch, `/status` `/cmd` `/ws` `/runs/latest` |
| MISSION CONTROL (dashboard) | Aikhan | `mission-control/` | Live HUD, teleop with E-STOP, browser TTS verdicts, replay, GLB viewer with pins from `data/spaces.json` |
| Coordinator (no code) | `<name>` | `data/` | Venue walk with tape + level, Scaniverse scans, `spaces.json`, pitch, Devpost |

Replace each `<name>` with the real owner.

## Run commands

```
# fake Scout: protocol v1 on :8080, loops data/runs/table-course.ndjson, logs every command
npm run fake                                   # (root) or: cd tools/fake-scout && npm install && npm start
npm run gen                                    # regenerate the sample run

# dashboard: Vite on :5173, serves data/ as static files
npm run dash                                   # or: cd mission-control && npm install && npm run dev

# Pi service (also runs on the Mac). Finds the ESP32 and lidar by probing USB serial ports.
cd pi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scout                      # :8080. SCOUT_ESP32_PORT / SCOUT_LIDAR_PORT override discovery ("none" disables)
.venv/bin/python tools/fake_esp32.py           # a fake ESP32 on a pty, for testing without hardware

# ESP32 bridge firmware: PlatformIO, board esp32dev (pipx install platformio; pio is in ~/.local/bin)
npm run flash                                  # or: cd firmware && pio run -t upload && pio device monitor -b 115200
cd firmware && pio run                         # compile only

# data
data/runs/*.ndjson      run logs and replay files (runs/index.json lists them for the dashboard)
data/models/*.glb       Scaniverse exports, under 30 MB, metres, Y up
data/spaces.json        spaces, checkpoints, pins (real building numbers only)
data/rules.json         limits, rule text, sources, table_scale
```

## Protocol v1 in ten lines (full text in docs/PROTOCOL.md)

- Scout is the Pi: `scout.local:8080`, HTTP + WS `/ws`, CORS `*`.
- `GET /status` (with `devices`), `POST /cmd`, `GET /cmd?c=forward|back|left|right|stop|beep`, `GET /runs/latest` (NDJSON).
- Commands: `drive v w`, `stop`, `mode`, `run start|stop`, `mark`, `zero`, `beep`, `config`. Also accepted as text frames on `/ws`.
- Teleop watchdog on the ESP32: no `drive` for 500 ms stops the motors. The dashboard resends every 100 ms while a key is held.
- `telem` at 10 Hz: `t mode measuring imu lidar pitch_deg roll_deg yaw_deg sweep[{a,mm}] width_mm bump stuck v w`. `imu`/`lidar` false means those readings are missing, not zero.
- `event`: `t seq kind value unit limit scale space`. Kinds: `slope_pass|fail`, `width_pass|fail`, `mark`, `run_start|stop`, `tilt_cutoff`.
- `value` is raw, `limit` is after scale, `scale` is what was applied (1.0 for slope). Full scale = `value / scale`.
- Table mode is `scale 0.25`, so the 860 mm width limit becomes 215. The dashboard shows a "Scale course 1:4" badge and never presents course numbers as building numbers.
- Run log NDJSON: header line `{"type":"run",...}`, then frames exactly as sent. Players time by `t` deltas.
- Pi to ESP32 serial (115200): text in (`D v w`, `S`, `Z`, `B p`, `L r g`, `T`), JSON lines out at 10 Hz.

## Rules

1. No scope creep. New ideas go in `docs/LATER.md`, not in code. The coordinator says no.
2. `docs/PROTOCOL.md` is frozen. A change needs sign-off from all three code owners, and the doc changes before the code.
3. All P0 across all workstreams before anyone starts P1. `docs/SPEC.md` has the cut order.
4. Commit small and often. `main` must always run. Never commit `node_modules`, `.venv`, `.pio` or `.env`.
5. Build against the fake Scout and the fake ESP32 first. Nobody waits for hardware.
6. Boring technology only. No new frameworks, no database, no cloud. Everything runs on one laptop and one Pi on a phone hotspot at 2.4 GHz.
7. Real numbers only, on screen and in the pitch, with the measured error stated. The 3D model is from the iPhone and the measurements are from Scout. Say so.
8. Scout has no cliff sensor and no odometry. Never run it near stairs or a table edge. Nothing may depend on knowing its position.
9. The Pi service must run with the lidar absent, the ESP32 absent, or both. A missing device disables its own feature only and is logged loudly at startup. Serial ports are found by probing, never by `/dev/ttyUSB0`.
10. A milestone is done when a human has run its acceptance test on the real laptop or the real robot. When two approaches work, pick the one a tired human can debug at hour 20.

## Claude Code notes

- Read `docs/PROTOCOL.md` before touching code that sends or receives frames or serial lines. Match the keys exactly.
- Pi service: Python 3.9+, pyserial + aiohttp only. `pi/scout/rplidar.py` is vendored from `~/dev/lidar-gaps` (not a git repo); edit there first. Threads for the two serial devices, asyncio for the server, immutable snapshots instead of locks.
- Firmware: `platform = espressif32@^6.9` (Arduino core 2.0.x, `ledcSetup`/`ledcAttachPin`), no libraries. Raw I2C for the MPU6050. Nothing in `loop()` blocks except the `T` self-test. Pins and sign flips live in `firmware/src/config.h` and mirror `hardware/PINMAP.md`.
- Dashboard: Vite + React + TypeScript, three.js through `@react-three/fiber` and `@react-three/drei`, `zustand`. Chrome only. Vite `publicDir` is `../data`.
- Keep code minimal and plain. One file that reads top to bottom beats a clever abstraction.
