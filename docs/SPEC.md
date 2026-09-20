# Scout spec, v2 (short)

Hack the North 2026. Submission Sunday 2026-09-20 08:00 EDT. The contract is `docs/PROTOCOL.md`; everything cut is in `docs/LATER.md`; `CLAUDE.md` has the run commands and the rules.

## What Scout is

A small rover that drives itself around a room like a Roomba and draws a 2D accessibility map of it. The lidar is its only sense: where the walls are, where obstacles are, and how wide the gaps between them are. There is no camera, so an obstacle is placed on the map and never named. Gaps are checked against the Ontario Building Code's 860 mm clear width and a failure gets a red light, a beep and a spoken verdict on the spot.

```
ESP32 (motors, watchdog)  <-- USB serial -->  Pi 4 (lidar, map, audit)  <-- hotspot wifi -->  dashboard (Chrome, laptop)
```

**Scout does not measure slope.** It has no IMU and no camera, and a ramp meeting a horizontal scan plane looks exactly like a wall, so a ramp is **never told apart from a wall and never judged**. Clearance width is the only building-code verdict Scout gives. Say this out loud in the pitch rather than letting a judge find it.

## The demo (3 minutes)

1. **Put Scout on the floor and drive.** It finds the walls and starts placing what it sees.
2. **Watch the map draw itself.** The room outline fills in on the big screen within a lap. Obstacles appear as Scout meets them.
3. **It meets the first obstacle**, drops a marker on the map where the lidar placed it, and goes around.
4. **It squeezes past the narrow gap** and calls it: red light, two low beeps, *"Too narrow between a wall and an obstacle. 51 centimetres. A wheelchair needs 86."*
5. **Close** on the finished map: the room measured to the centimetre, every obstacle marked, every barrier called.

Backup: replay mode plays a recorded run through the same dashboard, and a backup video.

## Workstreams

| Workstream | Delivers | Folder |
| --- | --- | --- |
| BODY | Chassis, wiring per `hardware/PINMAP.md`, motor driver, power, lidar mount | `hardware/` |
| BRAIN | ESP32 bridge firmware; Pi service: lidar, pose, map, clearance, wall following, protocol server | `firmware/`, `pi/` |
| MISSION CONTROL | Live 2D map, teleop with E-STOP, TTS verdicts, replay | `mission-control/` |
| Coordinator (no code) | Test room, measurements to check Scout against, pitch, video, Devpost | `data/` |

## Milestones and acceptance tests

P0 first, across all workstreams, before any P1.

| ID | P | Milestone | Acceptance test |
| --- | --- | --- | --- |
| B1 | P0 | Rolling chassis | Drives 1 m forward and back without the ESP32 resetting. |
| B2 | P0 | Motor driver and 5 V rail | **Open, see PINMAP.** Four motors run both ways from `T`; `vcgencmd get_throttled` stays `0x0` with the lidar spinning and the motors stalled. |
| B3 | P0 | Pi and lidar aboard | `python -m scout` logs both found; 5 minutes without a brownout or a lidar dropout. |
| B4 | P0 | Test room | A rectangular room, roughly 4 by 5 m, with three obstacles: one blocking the wall-following lane, one leaving a gap under 860 mm, one clear of the lane. Tape-measure every one. |
| F1 | P0 | Bridge firmware | `pio run -t upload`; monitor shows hello + 10 Hz lines; `D 0.4 0` drives and stops alone after 500 ms. |
| P1 | P0 | Pi service on the Mac | Devices found by probing; `/status.devices` all true; dashboard shows the live scan. Unplug either device: its feature drops, the rest keeps going, replug recovers. |
| P2 | P0 | Pose and map | Done on the simulator: room size within 10 mm of truth, position within 6 mm p95, 90% of scans placed, no false locks. Repeat in the real room: the drawn map matches a tape measure within 5 cm on both walls. |
| P3 | P0 | Clearance | Done on the simulator: a 510 mm slot reads 504 mm. Repeat on the real gap: within 3 cm of a tape, five runs in a row, and no false `width_fail` at a bare corner. |
| P4 | P0 | Wall following | Completes a lap of the test room without rescue, stops in front of each blocking obstacle, goes around it. |
| P5 | -- | Camera labels | **Dropped 2026-09-19.** Scout carries no camera. Every obstacle is placed by the lidar and reported unnamed. |
| P6 | P0 | Same on the Pi | `scout.local:8080` on the hotspot, systemd unit, survives a reboot. |
| M1 | P0 | Live 2D map | Done 2026-09-19: grid, live scan, robot, markers, clearance gauge, feed, TTS, link watchdog, replay, record. |
| M2 | P0 | Drive and E-STOP on the real robot | Keys drive it; release stops it; space stops it now; driving cancels wall following. |
| V1 | P0 | Accuracy check | Scout vs tape on five gaps and both room dimensions. Errors written into the pitch. Width error over 3 cm means fix the mount or the offset first. |
| R1 | P0 | Rehearsal | Five timed runs of the full demo with fresh batteries, a replay armed, a backup video open. |
| S1 | -- | Camera pre-flags candidates | Dropped with the camera. |
| S2 | P1 | Phone remote page on the Pi | Drive from a phone with the laptop closed. |
| S3 | P1 | Multi-room | Carry the room frame through a doorway. Needs scan matching, not rectangle fitting. |

## Cut order when behind (cut from the top)

1. Everything in `docs/LATER.md` stays there.
2. S3, S2, S1.
3. Spoken verdicts (keep the beep, the LED and the feed).

Never cut: the 2D map (M1, P2), clearance verdicts (P3), wall following (P4), replay mode, the backup video.

## Demo day

- Fresh cells. Spares on the table. Hotspot on, 2.4 GHz. Pi booted, `scout.local:8080` answering. Dashboard full screen, page clicked once so speech works, volume up.
- Room clear of feet and bags: people standing in it become obstacles and break the rectangle fit.
- A replay armed in the dropdown. Backup video in a tab.
- One person drives, one talks, one watches the robot, one handles questions.
- Scout will not connect: play the replay and say so. Scout misbehaves: E-STOP, drive it by hand, the audit still fires. Laptop dies: backup video on a phone.

## Honesty rules

Real numbers only, with the measured error stated. Scout never names an obstacle and never judges a ramp: it has no camera and cannot measure slope. Widths are Scout's own lidar measurements; say the error. One rule out of many: a screening tool for a human inspector, not a legal inspection. Not yet tested with wheelchair users; that is the first step after the weekend.

## Physical limits (do not plan against these)

No odometry, no IMU, no cliff sensor: never near stairs or a drop. Position comes from fitting the room's rectangle, so it works in one rectangular room and stops working in a corridor, a cluttered room, or through a doorway — `pose` goes false and the map stops growing until it recovers. Parking close to something large enough to hide a wall loses the fit. RPLIDAR A2M8: about 12 m range, 10 Hz, poor on glass, mirrors and shiny black. Hobby gearmotors: slow, unmatched, smooth floors only, and four-wheel skid steer scrubs hard through turns. Pi 4 needs a real 5 V / 3 A source.
