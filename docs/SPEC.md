# Scout spec, v1.1 (short)

Hack the North 2026. Submission Sunday 2026-09-20 08:00 EDT. The long original plan is superseded by this file, `docs/PROTOCOL.md` (the contract) and `docs/LATER.md` (everything cut). `CLAUDE.md` has the run commands and the rules.

## What Scout is

A small rover that audits indoor spaces for wheelchair accessibility. Its IMU measures floor slope and its lidar measures gap width. It compares both to Ontario Building Code limits (ramp at most 1 in 12, 4.76 degrees; doorways at least 860 mm) and calls out failures on the spot with a red light, a beep and a spoken verdict. A dashboard drives it, shows live readings, and pins every barrier onto a photoreal 3D model captured by an iPhone (Scaniverse) riding on Scout. Scout audits one space at a time with a person beside it; whole buildings on its own is the roadmap, not the demo.

```
ESP32 (motors, IMU, watchdog)  <-- USB serial -->  Pi 4 (RPLIDAR A1, audit, protocol server)  <-- hotspot wifi -->  dashboard (Chrome, laptop)
```

## The demo (3 minutes)

1. **Hook, real numbers.** "The route from the entrance to this table has N doorways. We measured them. K would stop a wheelchair." From the venue walk, tape measure and level app. Zero code.
2. **Live robot on the 1:4 table course.** The judge drives with the keys. On the 7 degree ramp Scout stops, measures, goes red and says "Ramp too steep. 7.1 degrees. The limit is 4.8." At the 190 mm gate: "Doorway too narrow. 76 centimeters at full scale." At the 250 mm gate: green chirp. The big screen shows the slope gauge and width bar crossing the limit line, with the "Scale course 1:4" badge.
3. **3D model.** A Scaniverse scan of a real corridor with red and green pins; click a pin for measurement, rule, source, fix.
4. **Close.** Building summary from `spaces.json`: spaces, checkpoints, barriers. One roadmap sentence.

Backup: replay mode plays a recorded run through the same dashboard, and a backup video.

## Workstreams

| Workstream | Delivers | Folder |
| --- | --- | --- |
| BODY | Chassis, wiring per `hardware/PINMAP.md`, power (three rails), Pi + lidar mount, phone cradle, the table course | `hardware/` |
| BRAIN | ESP32 bridge firmware; Pi service: serial, lidar, audit, protocol server, run buffer | `firmware/`, `pi/` |
| MISSION CONTROL | Live HUD, teleop with E-STOP, TTS verdicts, replay, GLB viewer with pins | `mission-control/` |
| Coordinator (no code) | Venue walk, Scaniverse scans, `data/spaces.json`, pitch, video, Devpost | `data/` |

## Milestones and acceptance tests

P0 first, across all workstreams, before any P1.

| ID | P | Milestone | Acceptance test |
| --- | --- | --- | --- |
| B1 | P0 | Rolling chassis | Drives 1 m forward and back on the floor without the ESP32 resetting. |
| B2 | P0 | Wired per pin map | `T` self-test runs both wheels the right way; tilt test gives the right signs; `imu:true`. |
| B3 | P0 | Pi + lidar aboard | Pi, lidar and ESP32 on the power bank; `python -m scout` logs both devices found; 5 minutes without a brownout. |
| B4 | P0 | Table course | 400 mm lane, 7 degree ramp with a landing, one adjustable gate (190 / 250 mm) at least 150 mm deep, tape marks. Scout fits through both settings. |
| F1 | P0 | Bridge firmware | `pio run -t upload`; monitor shows hello + 10 Hz lines; `D 0.4 0` drives, stops alone after 500 ms; `Z` zeroes. |
| P1 | P0 | Pi service on the Mac | Both devices found by probing; `/status.devices` all true; dashboard shows live pitch and sweep. Unplug either device: its feature drops, the rest keeps going, replug recovers. |
| P2 | P0 | Audit on the course | 7 degree ramp gives one `slope_fail` at 6 to 8 degrees. FAIL gate gives one `width_fail` within 10 mm of a ruler. PASS gate gives `width_pass`. Five runs in a row. |
| P3 | P0 | Same on the Pi | `scout.local:8080` on the hotspot, systemd unit, survives a reboot. |
| M1 | P0 | HUD on fake data | Done 2026-09-19: gauges, feed, TTS, link watchdog, replay, record. |
| M2 | P0 | Drive and E-STOP on the real robot | Keys drive it; release stops it; space stops it now. |
| M3 | P0 | 3D viewer with pins | The Scaniverse GLB loads in under 5 s; pins from `spaces.json` show with cards. |
| C1 | P0 | Venue walk | 8 to 10 real doors and ramps measured with tape and level, in `spaces.json`, one space scanned. |
| V1 | P0 | Accuracy check | Scout vs level app on five slopes, Scout vs tape on five gaps. Errors written into the pitch. Slope error over 1 degree or width error over 3 cm means fix the mount or the offset first. |
| R1 | P0 | Rehearsal | Five timed runs of the full demo with fresh batteries, a replay armed, a backup video open. |
| S1 | P1 | Tape measure in the 3D viewer | Click two points, distance within 3 cm of a tape. |
| S2 | P1 | Phone remote page on the Pi | Drive from a phone with the laptop closed. |
| S3 | P1 | Wall follow | Uses the lidar's front distance. Follows a corridor wall 3 minutes without rescue. |

## Cut order when behind (cut from the top)

1. Everything in `docs/LATER.md` stays there.
2. S3 wall follow, S2 remote page, S1 tape measure.
3. Pin placement UI (pins stay hand-typed in `spaces.json`).
4. Spoken verdicts (keep the beep, the LED and the feed).

Never cut: the table course audit (P2), the live HUD (M1), the 3D model with pins (M3), replay mode, the venue numbers (C1), the backup video.

## Demo day

- Fresh AAs. Power bank full. Spares on the table. Hotspot on, 2.4 GHz. Pi booted, `scout.local:8080` answering. Dashboard full screen, page clicked once so speech works, volume up.
- Course on its tape marks. `zero` on the flat part. TABLE MODE on, badge visible. A replay armed in the dropdown. Backup video in a tab.
- One person drives, one talks, one watches the robot, one handles questions.
- Scout will not connect: play the replay and say so. Scout misbehaves: E-STOP, drive it by hand, the audit still fires. Laptop dies: backup video on a phone.

## Honesty rules

Real numbers only, with the measured error stated. The 3D model is from the iPhone, the measurements are from Scout. The table course is 1:4 and labelled. Two rules out of many: a screening tool for a human inspector, not a legal inspection. Not yet tested with wheelchair users; that is the first step after the weekend.

## Physical limits (do not plan against these)

No odometry, no cliff sensor: never near stairs or a table edge, and nothing depends on knowing Scout's position. IMU pitch is trustworthy only when still, hence stop-and-measure. RPLIDAR A1: about 12 m range, 5.5 Hz, poor on glass, mirrors and shiny black. Hobby gearmotors: slow, unmatched, smooth floors only. Scaniverse: 2 to 3 minutes per space, manual export. Pi 4 needs a real 5 V / 3 A source.
