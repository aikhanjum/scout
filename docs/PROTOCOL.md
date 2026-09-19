# Scout protocol, version 2

This file is the contract between the ESP32 bridge firmware, the Pi service (BRAIN), the dashboard (Mission Control) and the fake Scout. It is frozen. To change it: agree with all three code owners, edit this file first, then the code, and add a line to the changelog at the bottom.

```
ESP32 (motors, watchdog)  <-- USB serial, section 9 -->  Pi 4 (lidar, camera, map, this protocol)  <-- wifi, sections 1 to 7 -->  dashboard
```

There is no Hub. The dashboard talks to Scout (the Pi) directly. Anything that speaks sections 1 to 7 (the Pi, `tools/fake-scout`, a replay file) is interchangeable to the dashboard.

**What Scout senses.** The lidar does geometry: where the walls are, where obstacles are, how wide the gaps are. The camera does naming: what a given obstacle is, including whether it is a ramp. Nothing measures slope, so a ramp is **labelled, never judged**. Clearance width is the only measurement checked against a building-code limit.

## 1. Network

- The Pi joins the hotspot named in its wifi config. Its hostname is `scout`, so it is `scout.local` on the hotspot (avahi, on by default in Raspberry Pi OS). The IP is also printed in the service log.
- HTTP on port **8080**. WebSocket at `/ws`. (8080, not 80, so the service never needs root. The fake Scout uses the same port on the laptop.)
- Every HTTP response carries `Access-Control-Allow-Origin: *`. `OPTIONS` on any path answers `204` with `Access-Control-Allow-Methods: GET, POST` and `Access-Control-Allow-Headers: Content-Type`.
- Consumers treat 2 s without a `telem` frame as link down, and reconnect forever.

## 2. Conventions

| Thing | Rule |
| --- | --- |
| `t` | Milliseconds since the Pi service started. Players time frames by `t` deltas and never assume a frame rate. |
| Distances | Millimetres, integers. `0` means no return or not valid. |
| Bearings | Degrees in the robot's frame. `0` is straight ahead, positive to the left, negative to the right, range -180..180. |
| `heading_deg` | The robot's heading in the room frame, -180..180, positive counter-clockwise seen from above. |
| Room frame | Origin at one corner of the fitted rectangle, `x_mm` along the room's longer wall, `y_mm` along the shorter, both positive inside the room. Fixed for the life of a run. |
| `v`, `w` | Forward speed and turn rate, each -1..1. `w` positive = left. |
| `value` | The raw measurement, always. |
| `limit` | The limit in effect for that measurement. |
| `label` | What the camera called a thing, lower case, free text (`"ramp"`, `"chair"`, `"door"`, `"unknown"`). Never used for a pass/fail decision. |

## 3. HTTP endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /status` | JSON below. |
| `POST /cmd` | One command (section 5) as the JSON body, any `Content-Type`. Replies `{"ok":true}` or `{"ok":false,"err":"..."}`. |
| `GET /cmd?c=<name>` | Browser-bar testing. `c` is one of `forward`, `back`, `left`, `right` (drive at 0.5), `stop`, `beep`, `roam`. Same reply as POST. |
| `GET /map` | The current map as one `map` frame (section 6), JSON. The same object Scout broadcasts on `/ws`. |
| `GET /photo/<id>` | A JPEG still captured when an obstacle was classified, or 404. |
| `GET /runs/latest` | The buffered run as NDJSON (section 7), `Content-Type: application/x-ndjson`. |
| `GET /` | Reserved for the phone remote page. Returns 404 in v2. |

`GET /status`:

```json
{"proto":2,"fw":"pi 0.2.0 / esp32 0.2.0","mode":"wall_follow","measuring":false,
 "run":{"active":true,"space":"E5 room 2024"},
 "devices":{"esp32":true,"lidar":true,"camera":true},
 "config":{"width_limit_mm":860,"robot_width_mm":260,"wall_target_mm":300,"cruise":0.4},
 "uptime_ms":123456,"ip":"172.20.10.4"}
```

`devices` says what is physically connected right now. The service runs with any of them missing; each missing device disables only its own feature (section 8).

## 4. Coordinate frames

Two frames, and everything on the wire says which one it is in.

- **Robot frame.** Used by `scan`. Bearings as in section 2: 0 ahead, positive left. Always valid.
- **Room frame.** Used by `pose`, `map` and every event position (`x_mm`, `y_mm`). Only valid while `pose` is `true`.

Scout has no odometry and no IMU. The room frame comes from fitting the four walls of the rectangular room in each lidar scan and reading position and heading off that rectangle directly. It does not drift, but it fails outright when fewer than two perpendicular walls are visible — then `pose` goes `false` and consumers must stop placing new points on the map until it comes back. **A false pose is worse than no pose; consumers must honour the flag.**

## 5. Commands

One JSON object per command. Accepted two ways:

- As the body of `POST /cmd`. Gets a reply.
- As a text frame on `/ws`. No reply. Use this for `drive`, so there is no HTTP round trip at 10 Hz.

```
{"cmd":"drive","v":0.6,"w":-0.3}     Teleop. Sets mode to teleop. v forward -1..1, w turn -1..1, positive = left.
{"cmd":"stop"}                       Stop now. Mode becomes idle. This is E-STOP.
{"cmd":"mode","mode":"wall_follow"}  idle | teleop | wall_follow.
{"cmd":"run","action":"start","space":"E5 room 2024"}   Clears the run buffer and the map, emits run_start.
{"cmd":"run","action":"stop"}                            Emits run_stop. The buffer and the map are kept.
{"cmd":"mark","label":"heavy door"}  Human checkpoint at Scout's current pose. Emits a mark event.
{"cmd":"map","action":"clear"}       Forget the map and the room frame, start mapping again from here.
{"cmd":"beep"}                       Link test.
{"cmd":"config","width_limit_mm":860,"robot_width_mm":260,"wall_target_mm":300,"cruise":0.4}
```

Rules:

- **Teleop watchdog.** In teleop, if no `drive` arrives for 500 ms, Scout stops. The watchdog lives on the ESP32 (section 9), so a Pi crash also stops the motors. The dashboard resends the current `drive` every 100 ms while a key or the joystick is held, and sends `stop` on release.
- `drive` sets mode to `teleop`, which cancels `wall_follow`. This is how a human takes over mid-run.
- `config` is partial: only the keys present change. `robot_width_mm` is a robot calibration; the dashboard sends limits but never sends `robot_width_mm`. Boot defaults are the values shown above.
- `run` only controls the buffer, the map and the `space` label. Mapping and auditing run whether or not a run is active.
- While `measuring` is true, `drive` is ignored and the motors stay stopped (up to about 3 s while the camera classifies).
- `drive` with no ESP32 connected replies `{"ok":false,"err":"esp32 not connected"}`. `mode wall_follow` with no lidar replies `{"ok":false,"err":"lidar not connected"}`.

## 6. WebSocket frames (Scout to dashboard, JSON text, one object per frame)

`telem`, 10 Hz:

```json
{"type":"telem","t":5123456,"mode":"wall_follow","measuring":false,"lidar":true,
 "scan":[1830,1825,0,1811,"... 360 entries ..."],
 "pose":true,"x_mm":1240,"y_mm":830,"heading_deg":-88.4,
 "room":{"w_mm":4210,"l_mm":5090},
 "gaps":[{"a0":15.0,"mm0":3083,"a1":18.0,"mm1":4696,"width_mm":1625,"span_deg":3.0,"mid_deg":16.5,"evidence":"see_through"}],
 "clearance_mm":742,"bump":[0,0],"stuck":false,"v":0.4,"w":0.0}
```

- `lidar` says whether the scan is live. When it is `false`, `scan` is `[]`, `pose` is `false` and `clearance_mm` is `0`.
- `scan` is exactly **360 integers** or empty. Index `i` is the range in millimetres at bearing `i` degrees counter-clockwise from straight ahead, so index `90` is left, `180` is behind, `270` is the right side (bearing -90). `0` means no return at that bearing. Each bin holds the second-smallest return within that degree, so one stray sample cannot fake an obstacle.
- `gaps` are the openings found in this scan, `[]` without a lidar. `a0`/`mm0` and `a1`/`mm1` are the two edges counter-clockwise, `width_mm` the straight-line distance between them, `mid_deg` the bearing of the middle. **`evidence` says how far the gap can be trusted**, and a consumer must never present anything but `see_through` as a measured opening:

  | `evidence` | Meaning |
  | --- | --- |
  | `see_through` | Something was seen past it, farther than both edges. It is really open. |
  | `step` | The two edges are neighbouring samples: a range step, such as the corner of an object. |
  | `unverified` | The arc between the edges is nothing but no-returns. An opening and a surface that does not reflect are indistinguishable in one rotation, so this is a candidate, not a measurement. |

- `pose` false means the room frame is not locked; `x_mm`, `y_mm`, `heading_deg` are `0` and must be shown as missing, not as the origin. `room` is `null` until the rectangle is fitted.
- `clearance_mm` is the width of the opening Scout is passing through right now, measured straight across its path, `0` when there is nothing to report. It is a live readout, not a verdict; verdicts come as `width_pass` / `width_fail` events. It is `0` in an open room (two walls four metres apart are not a gap anyone must fit through) and `0` while the way ahead is blocked (a gap Scout cannot drive towards is not a passage, and without that rule every corner of every room reads as a narrow doorway).
- `bump` and `stuck` are always `[0,0]` and `false` in v2. The keys stay so a later version can fill them without a protocol change.

`map`, 1 Hz while mapping, and once on connect:

```json
{"type":"map","t":5123456,"cell_mm":50,"w":96,"h":112,
 "origin":[0,0],"cells":"000011112222...","pose":true}
```

- An occupancy grid of the room, in the room frame. `w` columns by `h` rows, row-major, row 0 at `y_mm` 0.
- `cells` is a string of exactly `w * h` characters, one per cell: `0` unknown, `1` free, `2` occupied. A string, not an array, because it is a tenth the size and still readable in `curl`.
- `origin` is the room-frame position in millimetres of the centre of cell `(0,0)`.
- `cell_mm` is the cell edge length. 50 mm in v2.
- Consumers redraw on every `map` frame and keep the last one when the link drops.

`event`, when it happens:

```json
{"type":"event","t":5130000,"seq":12,"kind":"obstacle","label":"chair","confidence":0.71,"photo":"a1b2c3","x_mm":1980,"y_mm":640,"space":"E5 room 2024"}
{"type":"event","t":5141000,"seq":13,"kind":"ramp","label":"ramp","confidence":0.83,"photo":"d4e5f6","x_mm":3110,"y_mm":210,"space":"E5 room 2024"}
{"type":"event","t":5152000,"seq":14,"kind":"width_fail","value":780,"unit":"mm","limit":860,"between":"wall-obstacle","x_mm":2040,"y_mm":900,"space":"E5 room 2024"}
{"type":"event","t":5160000,"seq":15,"kind":"run_stop","space":"E5 room 2024"}
```

| `kind` | Extra fields | Meaning |
| --- | --- | --- |
| `obstacle` | `label`, `confidence`, `photo`, `x_mm`, `y_mm` | One per obstacle, the first time it is confirmed and named. |
| `ramp` | same as `obstacle` | The camera's top label was a ramp. Labelled only; Scout never judges a ramp. |
| `width_pass`, `width_fail` | `value`, `unit:"mm"`, `limit`, `between`, and `x_mm`, `y_mm` when placed | One per pinch point. `value` is the narrowest gap seen. |
| `mark` | `label`, `x_mm`, `y_mm` | Human-triggered, at Scout's current pose. |
| `run_start`, `run_stop` | none | |
| `bump`, `stuck` | reserved | Not emitted in v2. |

- `seq` counts up by one per event since the service started. It never repeats within a run.
- `space` is the current run's name, or `""` when no run is active.
- `between` is `"wall-wall"`, `"wall-obstacle"`, or `"unknown"` when Scout had no pose and so could not tell what made the gap. A width is a real measurement whether or not Scout knows where it is standing, so the verdict is still reported -- it simply arrives without `x_mm` and `y_mm` and cannot be pinned on the map.
- `confidence` is the classifier's score, 0..1. `label` is `"unknown"` and `confidence` is `0` when there is no camera or the score is below the floor in `config`.
- `photo` is an id; the still is at `GET /photo/<id>`. It is `""` when no image was kept.
- Events carrying a position are only emitted while `pose` is true.
- On every `width_pass` or `width_fail` Scout lights the LED (red for fail, green for pass) and beeps (fail: two low beeps, pass: one high chirp) through the ESP32. `obstacle` and `ramp` are silent — they are observations, not verdicts.

### Observable behaviour (what the dashboard can rely on)

1. **Obstacles.** A cluster of returns that is not part of the fitted room rectangle, at least 80 mm across and stable for 1 s, is an obstacle. Scout emits exactly one `obstacle` or `ramp` per obstacle per run, when it first stops in front of it. Re-seeing the same obstacle later emits nothing.
2. **Naming.** Before emitting, Scout stops, holds still, captures one still and classifies it. `measuring` is `true` and `v` is `0` throughout, up to about 3 s. With no camera it emits the event immediately with `label:"unknown"`.
3. **Width.** Scout measures every gap it can see between two returns that bound a passable opening — wall to wall, or wall to obstacle. When a gap narrower than 1.4 times the limit enters the path, a pinch opens; when it widens again, or after 3 s, the pinch closes and Scout emits exactly one `width_pass` or `width_fail` carrying the narrowest gap seen.
5. **Wall following.** In `wall_follow` Scout drives forward at `cruise`, holds `wall_target_mm` from the wall on its right, turns to follow corners, and reverses out of dead ends. It stops when it has closed a loop of the room. It never needs to know where it is to do this; `pose` only decides whether findings get placed on the map.
6. Events fire in every mode, run or no run. Everything except `mark` needs the lidar. A missing device silently disables its own feature and nothing else.

## 7. Run log (NDJSON)

One JSON object per line. Line 1 is the header. Every other line is a frame exactly as it went over `/ws`.

```
{"type":"run","space":"E5 room 2024","fw":"pi 0.2.0 / esp32 0.2.0","started_t":5000000,"config":{"width_limit_mm":860,"robot_width_mm":260,"wall_target_mm":300,"cruise":0.4}}
{"type":"telem", ...}
{"type":"map", ...}
{"type":"event", ...}
```

- `GET /runs/latest` returns the current buffer: every event, telemetry decimated to 2 Hz, and **only the most recent `map` frame**, written in place. The buffer holds at least 5 minutes. Events are never dropped; old telemetry is dropped first.
- A replay that contains no `map` frame shows an empty map. The dashboard's recorder keeps one map frame per 10 s so a recorded run replays with the map filling in.
- The dashboard records everything it receives on `/ws` into the same format and can save it as a file.
- `tools/fake-scout` serves any such file as if it were live. The dashboard's replay mode plays any such file with no server. Both time frames by `t` deltas.

## 8. Dashboard data files (in `data/`)

Not on the wire, but a contract between whoever edits the files and the dashboard.

`data/rules.json`:

```json
{
  "rules": [
    {"id":"door_clear_width","kind":"width","limit":860,"unit":"mm","cmp":"min",
     "text":"Doorways on an accessible route need at least 860 mm of clear opening. The same number applies to aisles.",
     "source":"Ontario Building Code 3.8.3.3",
     "fix":"Widen the opening, rehang the door, or move the obstruction."}
  ],
  "labels": ["ramp","door","chair","table","box","bin","cable","wall","unknown"]
}
```

- `kind` maps event kinds to rules: `width_*` to `door_clear_width`. `cmp:"min"` means the value must be at least the limit.
- `labels` is the closed set the classifier scores against, most specific first. Editing this file changes what the camera can say; no code change.
- There is no slope rule. Scout cannot measure slope, so it never judges a ramp.

`data/spaces.json` is gone. In v1 it held hand-measured building checkpoints for a viewer that no longer exists; Scout now produces its own map and its own findings at run time, and a saved run (section 7) is the record of a space. Nothing reads a spaces file.

## 9. Pi to ESP32 serial contract

USB serial, 115200 baud, 8N1, one message per line (`\n`). The Pi finds the port by probing every USB serial device for the ESP32's JSON lines, never by `/dev/ttyUSB0`.

Pi to ESP32, plain text:

```
D <v> <w>        drive. v, w in -1..1, w positive = left. No D for 500 ms: motors stop (the watchdog).
S                stop now, no ramp-down. Also clears the watchdog.
B [p]            beep. p 0 (default) one high chirp for pass, p 1 two low beeps for fail.
L <red> <green>  LEDs, 0 or 1 each.
T                self-test: each side forward and back, chirp, both LEDs. Blocks about 3 s.
```

ESP32 to Pi, JSON lines:

```
{"hello":"scout-esp32","fw":"0.2.0"}                     once at boot
{"t":5123456,"v":0.40,"w":0.00}                          10 Hz
{"err":"unknown cmd X"}                                  replies
```

- `t` is the ESP32's `millis()`. The Pi ignores it for timing and uses its own clock.
- The four motors are ganged as two sides: the left pair on one driver channel, the right pair on the other.
- Opening the port resets the ESP32 (DTR). It reboots in about a second, prints `hello`, and streams. The Pi tolerates the bootloader's non-JSON lines.

## Changelog

v2, 2026-09-19: no IMU. The lidar maps, the camera names, Scout roams on its own.

- **The IMU is gone.** `pitch_deg`, `roll_deg`, `yaw_deg` and the `imu` flag are removed from `telem`; `slope_pass`, `slope_fail` and `tilt_cutoff` are removed from events; the `zero` command and the `Z` serial line are removed; the slope rule is removed from `rules.json`. Nothing on Scout measures slope.
- **Ramps are labelled, not judged.** A `ramp` event says where a ramp is and how confident the camera was. It carries no angle and no pass/fail, because Scout cannot measure one. Clearance width is the only building-code judgement left.
- **The camera joins the protocol.** `devices.camera`, `label`, `confidence` and `photo` on obstacle events, `GET /photo/<id>`, and the `labels` list in `rules.json`.
- **`sweep` becomes `scan`**: the full 360-entry ring instead of five angles, so gap finding sees wall-to-obstacle pinches and not just the two sides. v1.2's separate 2 Hz `scan` frame is folded into `telem` -- one representation of a rotation on the wire, not two -- and its `gaps` with their `evidence` come with it, unchanged in shape.
- **Position and mapping added**: the room frame (section 4), `pose` / `x_mm` / `y_mm` / `heading_deg` / `room` in `telem`, the `map` frame, `GET /map`, `{"cmd":"map","action":"clear"}`, and positions on events.
- **`wall_follow` mode added**, with `wall_target_mm` and `cruise` in `config`. `roam` returns as a `GET /cmd` shortcut.
- **Table mode removed**: `scale`, `width_offset_mm` and `slope_limit_deg` are gone from `config`, and `table_scale` from `rules.json`. Scout now works at full scale in a real room, so there is no scaled number to label. `robot_width_mm` added.
- `width_*` events gain `between`, `x_mm`, `y_mm`. `heap` removed from `/status`.
- `proto` is now `2`. A v1 consumer and a v2 Scout will not interoperate; check `proto` and say so.

v1.1, 2026-09-19: the Pi becomes the brain. Additive, `proto` stayed 1.

- Scout's network endpoint became the Pi at `scout.local:8080`. The ESP32 lost wifi, HTTP and its WebSocket.
- The Pi to ESP32 serial contract was added; `devices` was added to `/status`; `sweep` came from the RPLIDAR A1 instead of a sonar turret.

v1, 2026-09-19: Hub removed, the dashboard speaks to Scout directly, commands accepted as `/ws` text frames, data file formats added.
