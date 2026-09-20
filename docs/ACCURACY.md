# Accuracy log

Real measurements only (rule 7). Each entry says what was measured, with what, and the error. Add to it; never delete a bad result.

## Doorway clear width, E7 6th floor, 2026-09-20 02:05 UTC

| | mm |
| --- | --- |
| iPhone Measure app | 880 |
| Scout, `width_pass` event as the lidar passed through (seq 12, "wall-wall") | 878 |
| **error** | **-2 mm (0.2 cm)** |

Method: RPLIDAR A2M8 carried on a laptop at chest height at a brisk walk, rear cone masked 35 degrees, no motors, no pose needed. The event value is the narrowest clearance seen across the pinch. Run file: `data/runs/e7-6th-floor-bathroom-2026-09-20-02-05-14.ndjson` (local; uploaded to Tiger Data as run `e6ae813557e0f5e5`).

Caveats, stated so nobody over-claims:

- Two door-sized passes fired 1.9 s apart on that walk, 1136 mm and 878 mm. The 878 mm one is assigned to the measured door because it is the door-sized one; the 1136 mm opening was not tape-measured.
- Earlier the same evening, standing still in a doorway by the desk with the lidar skewed about 30 degrees to the frame, clearance read a steady 850 mm (848 to 855 over 48 frames). If that was this same 880 mm door, the skew cost 30 mm (3.0 cm). `clearance()` measures across a slice perpendicular to the lidar's heading by design, so it reads low when the lidar is not square to the opening. Square it to the frame and the number is right.
- False fails in the same run: `width_fail` of 150 to 592 mm "wall-wall" at t = 62 to 74 s are the carrier's hands and the laptop lid inside the side sectors, not doorways. Carry the lidar clear of the body, or drive it.

## Position (SLAM) on the same walk

Route: desk to bathroom and back, about 15 to 20 steps each way (roughly 11 to 15 m). The lidar ended back at the start.

| | |
| --- | --- |
| scans with a pose | 58% (853 of 1467), longest blackout 5.5 s, no frame restarts |
| farthest point SLAM ever placed the lidar | 4.7 m from the start |
| final pose, lidar back at the start | 4.3 m away, heading 88 degrees off |
| empty lidar beams | 65% median in the corridor (31% at the desk) |

Verdict: SLAM did not follow the walk. It lost the corridor (the flat-surface gate refused, correctly) and re-locked onto the start room when the lidar came back, so the map from this run is not usable and the 17 mm drift figure from `check_slam.py` is simulation only and must not be quoted. `check_slam.py` cannot score a real run at all: a real file carries SLAM's own output, not ground truth. The honest test for a real lap is this one, return-to-start.

Next attempt, to change one thing at a time: a slow walk, lidar held clear of the body, two seconds still in each doorway.

## Walk 2 and the room loop, same evening

| | walk 2, corridor, slow, lidar in both hands | room loop, bathroom, lidar on one palm |
| --- | --- | --- |
| scans with a pose | 75%, longest blackout 10 s | 93%, longest blackout 2 s |
| farthest point SLAM placed the lidar | 4.7 m (route 11 to 15 m) | 6.8 m (a bathroom) |
| final pose, lidar back at the start | 0.36 m off but 103 degrees turned: a re-lock onto the start room, not tracking | 5.25 m off, 36 degrees turned: tracking was confident and wrong |
| path length SLAM drew | 72 m for a ~25 m round trip | 71 m for a small loop |
| empty beams, median | 58% | 47% |
| width verdicts | 38 fails of 260 to 280 mm "wall-wall" and no pass: the carrier's hands 13 to 17 cm either side were the walls | passes of 909, 921, 956, 973 mm on the bathroom door (tape not taken); fails of 150 to 360 mm inside, plausibly stalls and fixtures, not verified |

Verdict after three real attempts (brisk corridor, slow corridor, small room): SLAM did not follow the lidar in any of them. Do not demo a live map from it. Clearance verdicts are real when nothing is beside the sensor: carry it on a box or a flat palm from below, never by the sides.

## Tried and failed, so nobody repeats it: BreezySLAM (CoreSLAM/tinySLAM, RMHC), offline on the same recordings

No odometry, 800 px / 20 m map, defaults, 1 ms per scan. Return-to-start on the room loop: **3.1 m** (true: 0). Walk 2: **12.4 m** from the start after the return, farthest 12.4 m on an 11 to 15 m route. Same failure as `slam.py`, a different matcher. In its C code a distance of 0 is "no obstacle to max range", which carves free space through the walls that did not reflect (half the beams here). Not installed in the repo.

Conclusion for this hardware: without a motion source (wheel encoders or a gyro) no scan matcher we tried holds a position in these spaces. Position, and therefore the map, is out of scope until odometry exists. The live lidar view and the width verdict need no position and are unaffected.

## Lidar, what it sees

- Empty beams: 31% of the ring at the desk, 65% in the corridor. The clearance audit refuses a side that is more than 60% empty, so it produced a number on 100% of frames in the desk doorway and on 12% of frames along the corridor.
- Carrier behind the lidar: masked out by `SCOUT_MASK_BEHIND_DEG=35` (0 returns in that cone, verified). Hands and objects beside it are not masked.
