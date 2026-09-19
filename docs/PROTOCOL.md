# Scout protocol, version 1

This file is the contract between the ESP32 bridge firmware, the Pi service (BRAIN), the dashboard (Mission Control) and the fake Scout. It is frozen. To change it: agree with all three code owners, edit this file first, then the code, and add a line to the changelog at the bottom.

```
ESP32 (motors, IMU, watchdog)  <-- USB serial, section 8 -->  Pi 4 (lidar, audit, this protocol)  <-- wifi, sections 1 to 6 -->  dashboard
```

There is no Hub. The dashboard talks to Scout (the Pi) directly. Anything that speaks sections 1 to 6 (the Pi, `tools/fake-scout`, a replay file) is interchangeable to the dashboard.

## 1. Network

- The Pi joins the hotspot named in its wifi config. Its hostname is `scout`, so it is `scout.local` on the hotspot (avahi, on by default in Raspberry Pi OS). The IP is also printed in the service log.
- HTTP on port **8080**. WebSocket at `/ws`. (8080, not 80, so the service never needs root. The fake Scout uses the same port on the laptop.)
- Every HTTP response carries `Access-Control-Allow-Origin: *`. `OPTIONS` on any path answers `204` with `Access-Control-Allow-Methods: GET, POST` and `Access-Control-Allow-Headers: Content-Type`.
- Consumers treat 2 s without a `telem` frame as link down, and reconnect forever.
- A fallback access point is not in v1 (`docs/LATER.md`).

## 2. Conventions

| Thing | Rule |
| --- | --- |
| `t` | Milliseconds since the Pi service started. Players time frames by `t` deltas and never assume a frame rate. |
| Angles | Degrees. `pitch_deg` positive = nose up. `roll_deg` positive = right side down. `yaw_deg` positive = turned left (counter-clockwise seen from above). |
| Sweep angle `a` | 0 is straight ahead, negative is right, positive is left. |
| Distances | Millimetres. `0` means no return or not valid. |
| `v`, `w` | Forward speed and turn rate, each -1..1. `w` positive = left. |
| `value` | The raw measurement, always. |
| `limit` | The limit in effect for that measurement, after scale. |
| `scale` | The scale applied to that measurement. Always `1.0` for slope. The dashboard shows the full-scale number as `value / scale` and never presents a scaled course number as a building measurement. |

## 3. HTTP endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /status` | JSON below. |
| `POST /cmd` | One command (section 4) as the JSON body, any `Content-Type`. Replies `{"ok":true}` or `{"ok":false,"err":"..."}`. |
| `GET /cmd?c=<name>` | Browser-bar testing. `c` is one of `forward`, `back`, `left`, `right` (drive at 0.5), `stop`, `beep`. Same reply as POST. |
| `GET /runs/latest` | The buffered run as NDJSON (section 6), `Content-Type: application/x-ndjson`. |
| `GET /` | Reserved for the phone remote page (`docs/LATER.md`). Returns 404 in v1. |

`GET /status`:

```json
{"proto":1,"fw":"pi 0.1.0 / esp32 0.1.0","mode":"teleop","measuring":false,
 "run":{"active":true,"space":"Table course"},
 "devices":{"esp32":true,"imu":true,"lidar":true},
 "config":{"slope_limit_deg":4.76,"width_limit_mm":860,"scale":1.0,"width_offset_mm":0},
 "uptime_ms":123456,"heap":0,"ip":"172.20.10.4"}
```

`devices` says what is physically connected right now. The service runs with any of them missing; each missing device disables only its own feature (section 7).

## 4. Commands

One JSON object per command. Accepted two ways:

- As the body of `POST /cmd`. Gets a reply.
- As a text frame on `/ws`. No reply. Use this for `drive`, so there is no HTTP round trip at 10 Hz.

```
{"cmd":"drive","v":0.6,"w":-0.3}     Teleop. Sets mode to teleop. v forward -1..1, w turn -1..1, positive = left.
{"cmd":"stop"}                       Stop now. Mode becomes idle. This is E-STOP.
{"cmd":"mode","mode":"teleop"}       teleop | idle. (wall_follow and bounce are reserved names, not in v1.)
{"cmd":"run","action":"start","space":"Corridor A"}   Clears the run buffer, emits run_start.
{"cmd":"run","action":"stop"}                          Emits run_stop. The buffer is kept.
{"cmd":"mark","label":"heavy door"}  Human checkpoint. Emits a mark event.
{"cmd":"zero"}                       Calibrate level. Scout must be still on a flat floor for 1 s.
{"cmd":"beep"}                       Link test.
{"cmd":"config","slope_limit_deg":4.76,"width_limit_mm":860,"scale":1.0,"width_offset_mm":0}
```

Rules:

- **Teleop watchdog.** In teleop, if no `drive` arrives for 500 ms, Scout stops. The watchdog lives on the ESP32 (section 8), so a Pi crash also stops the motors. The dashboard resends the current `drive` every 100 ms while a key or the joystick is held, and sends `stop` on release.
- `config` is partial: only the keys present change. Effective width limit = `width_limit_mm * scale`. The slope limit is never scaled. `width_offset_mm` is added to the two side distances to get the width (0 when the lidar sits at the body's centre). The dashboard sends limits and scale on every connect and never sends `width_offset_mm`, which is a robot calibration. Boot defaults are the values shown above.
- `run` only controls the buffer and the `space` label. Auditing runs whether or not a run is active.
- While `measuring` is true, `drive` is ignored and the motors stay stopped (about 1.5 s).
- `drive` with no ESP32 connected replies `{"ok":false,"err":"esp32 not connected"}`.

## 5. WebSocket frames (Scout to dashboard, JSON text, one object per frame)

`telem`, 10 Hz:

```json
{"type":"telem","t":5123456,"mode":"teleop","measuring":false,"imu":true,"lidar":true,
 "pitch_deg":1.2,"roll_deg":0.3,"yaw_deg":87.5,
 "sweep":[{"a":-90,"mm":178},{"a":-45,"mm":655},{"a":0,"mm":1830},{"a":45,"mm":702},{"a":90,"mm":181}],
 "width_mm":359,"bump":[0,0],"stuck":false,"v":0.4,"w":0.0}
```

- `imu` and `lidar` say whether those readings are live. When `imu` is false, `pitch_deg`, `roll_deg`, `yaw_deg` are `0` and must be shown as missing, not as level. When `lidar` is false, `sweep` is `[]` and `width_mm` is `0`.
- `sweep` carries the latest reading for each angle Scout measures. From the lidar: the five angles `-90, -45, 0, 45, 90`, each the second-smallest return within ±5° of that bearing (robust to one stray sample), `0` when there is no return. Angles may be any subset of the five; consumers draw what they get.
- `width_mm` = right (`-90`) + left (`90`) + `width_offset_mm`, or `0` when either side is `0` or over 2000 mm.
- `bump` and `stuck` are always `[0,0]` and `false` in v1. The keys stay so a later version can fill them without a protocol change.

`scan`, 2 Hz, only while the lidar is connected (added in v1.2):

```json
{"type":"scan","t":5123456,"hz":9.6,"mode":"express",
 "pts":[[-90.0,178],[-89.7,0],[-89.4,802]],
 "gaps":[{"a0":-31.2,"mm0":844,"a1":-12.5,"mm1":829,"width_mm":812,"span_deg":18.7,"evidence":"see_through"}]}
```

- One whole rotation, for drawing. `telem.sweep` stays the five audited angles and is unchanged; nothing in the audit reads `scan`.
- `pts` is `[angle_deg, mm]` pairs in Scout's own convention (section 2: 0 ahead, negative right), angle to one decimal, ordered by angle. `mm` is `0` for no return, and those entries are kept: where the lidar saw nothing is information, not the absence of it.
- `hz` is the measured rotation rate and `mode` is `express` or `standard`, so a viewer can say how dense the data is.
- `gaps` are openings found between wall points, `a0` to `a1` counter-clockwise. `mm0` and `mm1` are the two edge ranges and `width_mm` the straight-line distance between them. The edges are carried here so a consumer never has to search `pts` for them.
- Only gaps between 150 mm and 3000 mm wide are reported. Below that is sensor noise, above it is open space rather than an opening. The lower bound is under the 1:4 course's 190 mm gate on purpose.
- `evidence` is how well supported the gap is, and a consumer must not present an `unverified` gap as a measured opening:

  | `evidence` | Meaning |
  | --- | --- |
  | `see_through` | Something was seen through the gap, farther than both edges. It is really open. |
  | `step` | The two edges are neighbouring samples. A range step, such as the corner of an object. |
  | `unverified` | The arc between the edges is nothing but no-returns. An opening and a surface that does not reflect look identical in one rotation, so this is a candidate, not a measurement. |

- At 2 Hz and about 420 points a rotation this is roughly 10 kB/s. A consumer that only drives and audits can ignore `scan` entirely.
- Width events never come from `scan`. They come from `telem.width_mm`, which is built from two real returns and is `0` when either side is missing, so an arc of no-returns can never become a measured width.

`event`, when it happens:

```json
{"type":"event","t":5130000,"seq":12,"kind":"slope_fail","value":7.1,"unit":"deg","limit":4.76,"scale":1.0,"space":"Table course"}
{"type":"event","t":5141000,"seq":13,"kind":"width_fail","value":190,"unit":"mm","limit":215,"scale":0.25,"space":"Table course"}
{"type":"event","t":5150000,"seq":14,"kind":"mark","label":"heavy door","space":"Table course"}
{"type":"event","t":5160000,"seq":15,"kind":"run_stop","space":"Table course"}
```

| `kind` | Extra fields | Meaning |
| --- | --- | --- |
| `slope_pass`, `slope_fail` | `value`, `unit:"deg"`, `limit`, `scale:1.0` | One per ramp, after a stop-and-measure. |
| `width_pass`, `width_fail` | `value`, `unit:"mm"`, `limit`, `scale` | One per pinch point. `value` is the minimum width seen. |
| `mark` | `label` | Human-triggered. |
| `run_start`, `run_stop` | none | |
| `tilt_cutoff` | `value`, `unit:"deg"` | Motors cut because pitch went over 20 degrees or roll over 15. |
| `bump`, `stuck` | reserved | Not emitted in v1. |

- `seq` counts up by one per event since the service started. It never repeats within a run.
- `space` is the current run's name, or `""` when no run is active.
- On every pass or fail event Scout also lights the LED (red for fail, green for pass) and beeps (fail: two low beeps, pass: one high chirp) through the ESP32.

### Observable audit behaviour (what the dashboard can rely on)

1. **Slope.** When the absolute pitch stays above 2 degrees for 500 ms, Scout stops, waits 400 ms, averages pitch for 1 s, and emits exactly one `slope_pass` or `slope_fail`. `measuring` is `true` and `v` is `0` throughout. It re-arms only after pitch has been under 1 degree for 1 s, so one ramp gives one event.
2. **Width.** A pinch opens when either source sees something narrower than 1.4 times the effective limit: `width_mm`, which is the corridor at Scout's own position, or a `see_through` gap in the scan that is ahead of Scout and close to it. The pinch closes as soon as neither source sees anything narrow, or 8 s after it opened, and Scout emits exactly one `width_pass` or `width_fail` carrying the minimum width either source saw. One doorway gives one event whether Scout drove through it or only looked at it. A gap counts only when its `evidence` is `see_through`; `unverified` gaps never reach the audit, and neither do `step` gaps.
3. Events fire in every mode, run or no run. Slope needs the IMU; width needs the lidar. A missing device silently disables its own audit and nothing else.

## 6. Run log (NDJSON)

One JSON object per line. Line 1 is the header. Every other line is a frame exactly as it went over `/ws`.

```
{"type":"run","space":"Table course","fw":"pi 0.1.0 / esp32 0.1.0","started_t":5000000,"config":{"slope_limit_deg":4.76,"width_limit_mm":860,"scale":0.25,"width_offset_mm":0}}
{"type":"telem", ...}
{"type":"event", ...}
```

- `GET /runs/latest` returns the current buffer: every event, plus telemetry decimated to 2 Hz. The buffer holds at least 5 minutes. Events are never dropped; old telemetry is dropped first.
- The dashboard records everything it receives on `/ws`, at full rate, into the same format, and can save it as a file.
- `tools/fake-scout` serves any such file as if it were live. The dashboard's replay mode plays any such file with no server. Both time frames by `t` deltas.

## 7. Dashboard data files (in `data/`)

Not on the wire, but a contract between whoever edits the files and the dashboard. Every value here is a real, full-scale building measurement. Table course numbers never go in these files.

`data/rules.json`:

```json
{
  "table_scale": 0.25,
  "rules": [
    {"id":"ramp_slope","kind":"slope","limit":4.76,"unit":"deg","cmp":"max",
     "text":"Ramps on an accessible route may be no steeper than 1 in 12 (4.76 degrees).",
     "source":"Ontario Building Code 3.8.3.4.(1)(b)",
     "fix":"Rebuild the ramp at 1 in 12 or shallower, or add a lift."},
    {"id":"door_clear_width","kind":"width","limit":860,"unit":"mm","cmp":"min",
     "text":"Doorways on an accessible route need at least 860 mm of clear opening. The same number applies to aisles.",
     "source":"Ontario Building Code 3.8.3.3",
     "fix":"Widen the opening, rehang the door, or move the obstruction."}
  ]
}
```

`kind` maps event kinds to rules: `slope_*` to `ramp_slope`, `width_*` to `door_clear_width`. `cmp:"max"` means the value must be at most the limit, `"min"` means at least.

`data/spaces.json`:

```json
{
  "spaces": [
    {
      "id": "corridor-a",
      "name": "Corridor A, 2nd floor",
      "model": "models/corridor-a.glb",
      "checkpoints": [
        {"id":"c1","rule":"door_clear_width","value":780,"unit":"mm","source":"tape","note":"Door to the washroom","position":[1.2,0.0,-3.4]},
        {"id":"c2","rule":"ramp_slope","value":3.1,"unit":"deg","source":"level","note":"Entrance ramp"},
        {"id":"c3","rule":null,"source":"mark","note":"Heavy door, no automatic opener","position":[0.4,0.0,-1.0]}
      ]
    }
  ]
}
```

- `model` is optional (a space measured by hand has none). The path is relative to `data/`.
- `position` is optional. A checkpoint with a position is drawn as a pin on the model: red fail, green pass, amber for `rule: null` (a note).
- `source` is one of `scout`, `tape`, `level`, `mark`. Pass or fail is computed by the dashboard from `rules.json` and never stored.
- The summary banner counts spaces, checkpoints, and checkpoints that fail.

Models: GLB (glTF 2.0), metres, Y up, true scale as Scaniverse exports them. Pin positions are metres in the model's own frame. Keep each file under 30 MB (`npx @gltf-transform/cli optimize in.glb out.glb --texture-size 2048`, once, by hand).

## 8. Pi to ESP32 serial contract

USB serial, 115200 baud, 8N1, one message per line (`\n`). The Pi finds the port by probing every USB serial device for the ESP32's JSON lines, never by `/dev/ttyUSB0`.

Pi to ESP32, plain text:

```
D <v> <w>        drive. v, w in -1..1, w positive = left. No D for 500 ms: motors stop (the watchdog).
S                stop now, no ramp-down. Also clears the watchdog.
Z                zero: store the current pitch and roll as level and reset yaw. Robot still.
B [p]            beep. p 0 (default) one high chirp for pass, p 1 two low beeps for fail.
L <red> <green>  LEDs, 0 or 1 each.
T                self-test: each motor forward and back, chirp, both LEDs. Blocks about 3 s.
```

ESP32 to Pi, JSON lines:

```
{"hello":"scout-esp32","fw":"0.1.0"}                                                    once at boot
{"t":5123456,"pitch":1.2,"roll":0.3,"yaw":87.5,"v":0.40,"w":0.00,"imu":true}           10 Hz
{"zeroed":true}     {"err":"unknown cmd X"}                                             replies
```

- `t` is the ESP32's `millis()`. The Pi ignores it for timing and uses its own clock.
- `imu` is false when the MPU6050 does not answer on I2C. The Pi passes it through as `imu` in telemetry.
- `pitch`, `roll`, `yaw` follow section 2. Sign flips for mounting live in `firmware/src/config.h`, not on the Pi.
- Opening the port resets the ESP32 (DTR). It reboots in about a second, prints `hello`, calibrates the gyro for 2 s (robot still), and streams. The Pi tolerates the bootloader's non-JSON lines.

## Changelog

v1.3, 2026-09-19: gaps can open a width pinch. Additive, `proto` stays 1. **Needs the same sign-off.**

- Section 5, observable audit behaviour 2: the width pinch now takes a second input, a `see_through` gap from the scan that is ahead of Scout and close to it. Frames, kinds and fields are unchanged; only when an event fires can differ.
- One doorway still gives exactly one event. The two sources feed one pinch and the event carries the minimum either saw, so a doorway Scout drives through is still measured by `width_mm` as before. Seeing a gate ahead and then driving through it is one continuous pinch, not two.
- The pinch's hard cap goes from 3 s to 8 s, because an approach plus the drive through is longer than 3 s. It still closes the instant neither source sees anything narrow, so verdict timing at the gate is unchanged.
- `unverified` gaps never reach the audit, by construction. An arc of no-returns cannot become an event, only a candidate drawn in the view.
- Thresholds live in `pi/scout/audit.py`: a gap counts when its middle bearing is within 60 degrees of straight ahead and both edges are within 2500 mm.

v1.2, 2026-09-19: the `scan` frame. Additive, `proto` stays 1. **Needs sign-off from all three code owners before it is final.**

- `scan` added to section 5: one full rotation plus detected gaps, at 2 Hz, for the dashboard's lidar view. Nothing else reads it and the audit is untouched.
- Gaps carry `evidence`, because a lidar cannot tell an opening from a non-reflective surface in a single rotation. `unverified` gaps must never be shown as measurements.
- No change to `telem`, `event`, commands, `/status` or the serial contract.

v1.1, 2026-09-19: the Pi becomes the brain. Additive, `proto` stays 1.

- Scout's network endpoint is the Pi at `scout.local:8080`. The ESP32 no longer has wifi, HTTP or a WebSocket.
- Section 8 added: the Pi to ESP32 serial contract.
- `devices` added to `/status`; `imu` and `lidar` added to `telem`; `fw` now names both halves.
- `sweep` comes from the RPLIDAR A1: all five angles, second-smallest return within ±5°. The sonar turret and servo are gone.
- `width_offset_mm` default is `0` (lidar at the body centre).
- `drive` with no ESP32 returns an error.
- LED and beep on pass/fail events made explicit.
- Fallback access point moved to LATER.

v1, 2026-09-19, relative to section 4 of the original master spec:

- Hub removed. Section 4.6 (Hub API) is gone; the dashboard speaks this protocol directly. The `link` and `verdict` frames are gone (the dashboard keeps its own link state and speaks verdicts itself).
- Commands are also accepted as text frames on `/ws`. `drive` implies teleop.
- `sweep` may carry a subset of the five angles.
- `scale` added to measurement events, and `config` to the run header, so replay files are self-describing.
- `measuring` and `config` added to `/status`.
- `turn` command and the `roam` shortcut removed (no wall-follow in v1). `config` keys `wall_target_mm`, `cruise`, `stop_at_wall` removed.
- `seq` counts since boot rather than per run.
- `GET /` (phone remote page) deferred.
- Data file formats (`rules.json`, `spaces.json`) added as section 7.
