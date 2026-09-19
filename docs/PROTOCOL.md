# Scout protocol, version 2

This file is the contract between the motor-board firmware, the Pi service (BRAIN), the dashboard (Mission Control) and the fake Scout. It is frozen. To change it: agree with all three code owners, edit this file first, then the code, and add a line to the changelog at the bottom.

```
RedBoard (motors, watchdog)  <-- USB serial, section 9 -->  Pi 4 (lidar, camera, map, this protocol)  <-- wifi, sections 1 to 7 -->  dashboard
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
{"proto":2,"fw":"pi 0.2.0 / motor scoutable-motor-v1 ready","mode":"wall_follow","measuring":false,
 "run":{"active":true,"space":"E5 room 2024"},
 "devices":{"esp32":true,"motor":true,"lidar":true,"camera":true},
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

- **Teleop watchdog.** In teleop, if no `drive` arrives for 600 ms, Scout stops. The watchdog lives on the motor board (section 9), so a Pi crash also stops the motors. The dashboard resends the current `drive` every 100 ms while a key or the joystick is held, and sends `stop` on release.
- `drive` sets mode to `teleop`, which cancels `wall_follow`. This is how a human takes over mid-run.
- `config` is partial: only the keys present change. `robot_width_mm` is a robot calibration; the dashboard sends limits but never sends `robot_width_mm`. Boot defaults are the values shown above.
- `run` only controls the buffer, the map and the `space` label. Mapping and auditing run whether or not a run is active.
- While `measuring` is true, `drive` is ignored and the motors stay stopped (up to about 3 s while the camera classifies).
- `drive` with no motor board connected replies `{"ok":false,"err":"motor board not connected"}`. `mode wall_follow` with no lidar replies `{"ok":false,"err":"lidar not connected"}`.

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
- On every `width_pass` or `width_fail` Scout **would** light an LED and beep, but the board that is fitted has neither (section 9), so the verdict reaches a human through the dashboard and its speech only. `obstacle` and `ramp` are silent either way — they are observations, not verdicts.

### Observable behaviour (what the dashboard can rely on)

1. **Obstacles.** A cluster of returns that is not part of the fitted room rectangle, at least 80 mm across and stable for 1 s, is an obstacle. Scout emits exactly one `obstacle` or `ramp` per obstacle per run, when it first stops in front of it. Re-seeing the same obstacle later emits nothing.
2. **Naming.** Before emitting, Scout stops, holds still, captures one still and classifies it. `measuring` is `true` and `v` is `0` throughout, up to about 3 s. With no camera it emits the event immediately with `label:"unknown"`.
3. **Width.** Scout measures every gap it can see between two returns that bound a passable opening — wall to wall, or wall to obstacle. When a gap narrower than 1.4 times the limit enters the path, a pinch opens; when it widens again, or after 3 s, the pinch closes and Scout emits exactly one `width_pass` or `width_fail` carrying the narrowest gap seen.
5. **Wall following.** In `wall_follow` Scout drives forward at `cruise`, holds `wall_target_mm` from the wall on its right, turns to follow corners, and reverses out of dead ends. It stops when it has closed a loop of the room. It never needs to know where it is to do this; `pose` only decides whether findings get placed on the map.
6. Events fire in every mode, run or no run. Everything except `mark` needs the lidar. A missing device silently disables its own feature and nothing else.

## 7. Run log (NDJSON)

One JSON object per line. Line 1 is the header. Every other line is a frame exactly as it went over `/ws`.

```
{"type":"run","space":"E5 room 2024","fw":"pi 0.2.0 / motor scoutable-motor-v1 ready","started_t":5000000,"started_at":"2026-09-19T19:04:11.250Z","config":{"width_limit_mm":860,"robot_width_mm":260,"wall_target_mm":300,"cruise":0.4}}
{"type":"telem", ...}
{"type":"map", ...}
{"type":"event", ...}
```

- `GET /runs/latest` returns the current buffer: every event, telemetry decimated to 2 Hz, and **only the most recent `map` frame**, written in place. The buffer holds at least 5 minutes. Events are never dropped; old telemetry is dropped first.
- `started_at` is the wall clock (ISO 8601, UTC) at which `t` was `started_t`, so a frame's real time is `started_at + (t - started_t)` ms. Files written before v2.1 do not have it; a consumer falls back to the file's modification time as the end of the run.
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

`data/history.json` (written by `tools/upload-run`, read by the dashboard's "Room over time" panel; absent means the panel is off):

```json
{"generated_at":"2026-09-19T20:10:00Z","bucket":"1 hour","includes_simulated":false,
 "spaces":[{"space":"E5 corridor","buckets":[
   {"start":"2026-09-19T19:00:00Z","runs":1,"frames":660,"min_clearance_mm":504,"fails":2,"empty_share":0.03}]}]}
```

- One entry per space, one bucket per `time_bucket` of run time. `min_clearance_mm` is the narrowest valid clearance in the bucket (`null` if none was valid), `fails` counts `*_fail` events, `empty_share` is the fraction of lidar samples with no return.
- Runs whose `fw` starts with `fake` are simulated. They are never in this file unless it was generated with `--simulated`, and then `includes_simulated` says so.

## 9. Pi to motor board serial contract

USB serial, 115200 baud, 8N1, one message per line (`\n`). The Pi finds the port by probing every
USB serial device for the board's boot banner, never by `/dev/ttyUSB0`.

**The board is a SparkFun RedBoard** (ATmega328P, Uno clone, CH340 USB) carrying a DK Electronics
motor shield (Adafruit v1 clone: 2x L293D behind an SN74HC595). It took the role the ESP32 was
meant to have; the ESP32 never arrived. Its firmware is `06_serial_drive.ino` and it is owned by
the firmware workstream.

**The board's dialect is its own, and the Pi translates.** `pi/scout/redboard.py` still accepts the
lines below from the rest of the service and converts them; nothing above that file knows which
board is fitted. The lines the service uses internally are unchanged:

```
D <v> <w>        drive. v, w in -1..1, w positive = left.
S                stop now. Also clears the watchdog.
B [p]            beep.  NOT WIRED: this board has no buzzer. Accepted and discarded.
L <red> <green>  LEDs.  NOT WIRED: this board has no indicator LEDs. Accepted and discarded.
T                self-test. NOT WIRED.
```

On the wire, after translation:

```
<left> <right>   drive, each -255..255, positive forward, skid steer. Left = v-w, right = v+w,
                 both divided by the overshoot when either saturates, so a turn keeps its ratio.
s                stop now
?                status
```

Board to Pi, plain text:

```
scoutable-motor-v1 ready     banner, once, on every port open
ok <left> <right>            accepted, with the clamped values actually applied
err                          not parseable
watchdog stop                nothing received for 600 ms, the board stopped itself
```

- **The watchdog is 600 ms**, not the ESP32's 500 ms. The dashboard resends every 100 ms while a
  key is held and wall following sends at 10 Hz, so both satisfy it. It is the only thing that
  stops the motors if the Pi service dies, so nothing may pet it on a timer while Scout moves.
- **The board only speaks when spoken to.** It does not stream, so silence cannot mean "gone" on
  its own. The Pi sends `?` after one second of its own silence and treats four seconds without a
  reply as a disconnect. The keep-alive is suppressed whenever a `D` was sent recently.
- **Opening the port resets the board** (DTR). It is deaf for about 1.5 s, so the Pi waits for the
  banner before reporting `devices.motor` true; commands written before then are lost.
- **Any non-zero duty below 70 is raised to 70 by the firmware**, because the gearboxes hum without
  turning below that. So the Pi sends a real `0` for anything under a duty of 8 rather than letting
  a creep be inflated ninefold into a lurch.
- The four motors are ganged as two sides: the left pair on one channel, the right pair on the
  other. There is no per-wheel control and none is needed.
- `bump` and `stuck` in `telem` are always `[0, 0]` and `false`. This board reports neither.

### What this costs, and it is demo-visible

The spec says a failed clearance gets **a light, a beep and a spoken verdict**. This board has no
buzzer and no LEDs, so **two of those three channels do not exist** and the verdict reaches a human
through the dashboard and its speech alone. `redboard.py` logs the absence loudly once per command
kind rather than failing silently. Restoring them is a firmware job: the shield leaves D2 and D3
free, and a piezo on one of them would return the beep.

## Changelog

v2.2, 2026-09-19: the motor board is a RedBoard, not an ESP32. Additive; `proto` stays `2`.

- **Section 9 is rewritten for the board that exists.** The ESP32 never arrived; a SparkFun
  RedBoard with a DK Electronics shield took the role and was already working. The Pi translates
  in `pi/scout/redboard.py`, so sections 1-8 are untouched and the dashboard needs no change.
- **`devices.motor` added to `/status`**, alongside `devices.esp32`, which keeps its name and its
  meaning so existing consumers keep working. Both report the same board. New consumers should
  read `motor`; `esp32` will be removed once nothing reads it.
- **`B`, `L` and `T` are accepted and discarded.** This board has no buzzer, no LEDs and no
  self-test, so a width verdict is now spoken and shown, but not beeped or lit.
- The watchdog is **600 ms**, not 500. Nothing had to change to satisfy it.
- `SCOUT_MOTOR_PORT` is the new name for `SCOUT_ESP32_PORT`. The old name still works.

v2.1, 2026-09-19: `started_at` added to the run header (section 7) and `data/history.json` defined (section 8). Additive, `proto` stays 2.

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
- The Pi to ESP32 serial contract was added; `devices` was added to `/status`; `sweep` came from the RPLIDAR A2M8 instead of a sonar turret.

v1, 2026-09-19: Hub removed, the dashboard speaks to Scout directly, commands accepted as `/ws` text frames, data file formats added.
