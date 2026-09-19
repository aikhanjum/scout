# Handoff, 2026-09-19 07:15 EDT

Submission is **Sunday 2026-09-20 08:00 EDT** — roughly **25 hours** from this timestamp.

Two audiences. **Sections 1–3 are for a teammate** who has been heads-down on something else and needs to know what Scout is now and where it stands. **Sections 4 onward are the working detail** for whoever picks up the code next.

`CLAUDE.md` has the rules, `docs/PROTOCOL.md` is the contract, `RUNBOOK.md` is demo day.

---

## 1. Read this first: Scout changed direction

### The thing that forced it

**There is no IMU and there never will be.** Confirmed by the team this session.

That is not a missing part, it is a change of what Scout can claim. A ramp meeting a horizontal lidar plane returns exactly what a wall returns — same points, same shape. Nothing on Scout can measure slope. So:

> **Ramps are labelled by the camera and never judged. Clearance width is the only building-code verdict Scout gives.**

The old demo beat — *"Ramp too steep. 7.1 degrees. The limit is 4.8."* — **is gone and cannot come back.** If you are still building or pitching against slope, stop.

### What Scout is now

A Roomba that draws an accessibility map. It drives itself around a room, maps it in 2D, and marks what it finds.

```
ESP32 (motors, watchdog)  <--USB-->  Pi 4 (lidar, camera, map, audit)  <--wifi-->  dashboard
```

The division of labour is the whole design, and it is worth holding in your head:

| | Job | Produces |
| --- | --- | --- |
| **Lidar** | **Geometry.** Where walls are, where Scout is, where obstacles are, how wide gaps are. | the map, the pose, the clearance verdicts |
| **Camera** | **Names.** What a given obstacle is, including whether it is a ramp. | `label`, `confidence`, a photo |

The camera never measures anything and never decides a pass/fail. Its only job is turning *"an obstacle at (2400, 4500)"* into *"a ramp at (2400, 4500)"*.

### How Scout gets a position without odometry or an IMU

This is the part most likely to surprise you. Nothing on the robot knows how far it has driven. Position comes out of the lidar itself: **fit the rectangle of the room in every scan and read position and heading straight off it.** Nothing accumulates, so nothing drifts — but it fails outright in spaces that are not rectangular, and then `pose` goes false and the map stops growing until it recovers.

**If you consume telemetry: honour `pose: false`.** When it is false, `x_mm`/`y_mm`/`heading_deg` are zero and mean nothing. A confidently wrong pose corrupts the map for the rest of the run.

### The demo, now

1. Put Scout on the floor, press **ROAM**.
2. It finds the walls and starts following the right-hand wall.
3. The map draws itself on the big screen within a lap.
4. It stops in front of an obstacle, photographs it, names it, drops a labelled marker, goes around.
5. It squeezes through a narrow gap and calls it: red light, two beeps, *"Too narrow between a wall and an obstacle. 51 centimetres. A wheelchair needs 86."*

---

## 2. Status at a glance

| | State |
| --- | --- |
| Protocol v2, Pi brain, firmware, dashboard, simulator | **Done and pushed.** 15 commits on `main`. |
| Firmware build | **Compiles clean.** Never flashed to a board. |
| Lidar | **Works.** Spins, reports, express mode. Unplugged at session end. |
| Pose / map / clearance / wall following | **Validated only in simulation.** The one real-world test failed — see §4. |
| Camera / CLIP labels | **Never executed.** No Pi, no camera, no model files. Degrades to `"unknown"`. |
| Raspberry Pi | **Never used.** Not flashed, no hotspot, no systemd. |
| Motor driver | **Does not exist.** Blocks driving only. |
| 5 V rail | **Does not exist.** 18650s cannot feed a Pi 4. |

Numbers that are real, all from the simulated fixture: room size within 10 mm, position 4.5 mm median / 14 mm worst, heading within 0.2°, on 88% of scans; a 510 mm slot read as **504 mm**; 3 ms per scan.

**The honest summary: the software is finished and the robot does not exist yet.** The single real-world contact with a lidar failed, and that is §4.

---

## 3. What this means for your workstream

### If you wrote `gaps.py` / `LidarView.tsx` / the express-scan driver

**Nobody has told you the IMU is gone and `main` is now protocol v2.** Sorry — that conversation is still owed, and this paragraph is the substitute.

**Your work survived intact.** Nothing was deleted:

- `rplidar.py` — kept wholesale; v2's lidar thread runs on your driver unchanged, so mapping gets express scan for free.
- `gaps.py` — kept and wired in. It feeds both the lidar view *and* the width audit.
- `LidarView.tsx` — kept, rebuilt to read `telem.scan` / `telem.gaps`, and sits under the map as a raw-data companion.
- `check_audit.py` — **all 17 checks pass** against v2.
- `RUNBOOK.md` — kept, with the slope narration corrected.

**Your `evidence` model was better than what I first wrote and is now load-bearing.** A gap bounded by an arc of no-returns can never become a measurement, because a doorway and a non-reflective surface are identical in one rotation. I extended the same idea to the clearance measurement.

**What did change:** protocol v1.2's separate 2 Hz `scan` frame is folded into `telem` as a fixed 360-int array, so a rotation has one representation on the wire rather than two. `proto` is now `2` — a v1 consumer and a v2 Scout will not interoperate.

### If you are on hardware

Two things have been open all session and block the *driving* half of the demo:

1. **A motor driver.** Four DAGU motors, nothing to drive them. The firmware expects a two-channel H-bridge, left pair on channel A, right pair on B.
2. **5 V regulation.** 18650s are 7.4–8.4 V; a Pi 4 needs a real 5 V at 3 A. Target a 5 A buck — **not an LM2596**, whose "3 A" is a peak figure that sags into brownout territory.

*Shortcut that removes item 2 entirely:* put the Pi and lidar on a **USB power bank** and give the 18650s to the motors alone.

**Know this failure mode:** an undersized 5 V rail does not crash the Pi cleanly. It browns out the USB ports first, so the **lidar drops out at random and it reads as a software bug**. `vcgencmd get_throttled` → `0x0` healthy, bit 0 = under-voltage now, bit 16 = happened since boot. Check it before anyone blames code.

**Mounting matters more than it sounds:** the lidar must be level, at the top, with nothing of the robot above its beam plane. Anything poking up becomes a permanent wall in every scan and the room fit dies.

### If you are on the dashboard

The slope gauge and the GLB/Scaniverse viewer are gone. The 2D map canvas is the screen now (plain canvas, no three.js). `LidarView` sits beneath it. Clearance shows `--` rather than a number when there is nothing to report — that is normal in open space and when a side is too blind to trust.

### If you are on the pitch

Say all of this out loud rather than letting a judge find it:

- Scout labels ramps and never judges them, because it cannot measure slope.
- Widths are Scout's own lidar measurements — quote the error.
- The replay is a **simulated room with scripted labels**, not a recording of the robot. `RUNBOOK.md` already says to state this.
- Two rules out of many; a screening tool for a human inspector, not a legal inspection. Not yet tested with wheelchair users.

---

## 4. The live problem: Scout cannot localise in the arena we built

This is where the session stopped, and it is the critical path.

### What happened

The test arena is **four tables laid on their sides** forming a pen, inside an open lab. Not a room.

`pi/scout/pose.py` on `main` fits the **convex hull** of a scan and takes its minimum-area rectangle. That works beautifully in a closed room and **fails completely in the pen**: the hull is defined by the most distant returns, and in an open arena those are beams escaping through the gaps between tables. On a real scan from inside the pen it produced a 12.5 × 7.5 m rectangle with **3%** of returns on it, and never produced a pose.

The room *is* in the data. RANSAC on that same scan finds four genuine walls — two parallel pairs **86° apart**, the longest 7.6 m carrying 18% of the scan. The hull method simply cannot see them.

### What was built, and why it is not merged

Branch **`pose-arena-fit`**, pushed. `main` is untouched.

It sweeps orientation and reads the two opposite walls off the projection histogram **two ways** — trimmed extremes, and range-weighted peaks — keeping whichever puts more of the scan on the resulting rectangle.

| | Table pen (real scan) | Simulated closed room |
| --- | --- | --- |
| hull fit (on `main`) | 3% on walls, **never locks** | 1.6 mm median, 88% of scans, 0 relocks |
| branch fit | **76% on walls, locks** | ~100 mm median, 83% of scans, 1 relock |

**It fixes the real environment and makes the clean-room case markedly worse.** That trade was never accepted, which is why it sits on a branch.

### The one measurement that unblocks it

**Nobody has measured the pen with a tape.** Two variants of the fit gave **2280 × 2760 mm** and **1462 × 1565 mm** for the same scan. They cannot both be right, and without ground truth the 76% may be flattering a wrong answer.

**Do this before touching the algorithm again.** Then run `cd pi && .venv/bin/python tools/check_fit.py` on the branch and see which matches.

Worth raising with the team: a pen 1.5–2.5 m across, against an 860 mm limit, has room for about one gap and almost no driving. Fine for proving pose and clearance; thin as a demo arena. Bigger tables or a small real room would be better.

---

## 5. Six approaches that failed, and why — do not repeat them

1. **Plain point counts** → picks whatever is nearest. A lidar samples at a fixed angular step, so a surface returns points in inverse proportion to distance. A chair at 1.4 m beat the room wall at 3.9 m.
2. **Range-weighted counts alone** → overcorrects. Two strays 9 m down a corridor outvote a real wall.
3. **Scoring a wall by how far its returns run along it** → cannot discriminate. Every bin on one axis picks up the two *parallel* walls crossing it, so every bin scores the full room length.
4. **Quantile-trimmed extremes (2%)** → deletes sparse walls. In the simulated room a ramp stands against the far wall leaving it **eleven** returns; trimming seven removes the wall.
5. **Maximising on-edge fraction alone** → genuinely ambiguous. The *true* rectangle scores 83%; a wrong one cutting across the ramp's face scores **84%**. Hence the area tie-break — a room contains its furniture.
6. **Requiring the rectangle to contain the scan** → right for a closed room, wrong for the pen, where 43% of returns legitimately escape through the gaps.

Two constraints that did help and should be kept: **walls must straddle the lidar** (it sits inside the room), and **ties go to the larger rectangle**.

---

## 6. Tools and fixtures

```
cd pi && .venv/bin/python tools/check_pose.py      # why is there no pose, on a LIVE Scout
cd pi && .venv/bin/python tools/check_fit.py       # branch only: score the fit on both fixtures
cd pi && .venv/bin/python tools/check_audit.py     # 17 clearance and gap checks
```

`check_pose.py` reads one real scan over the WebSocket (the lidar port is held exclusively by the service) and walks it through every gate in `pose.py`, printing what each saw and wanted. It saves the scan to `pi/tools/scan-dump.json` — **that dump is how a scan gets handed to someone not standing in the room.**

`check_fit.py` (branch) scores any change to `pose.py` against both fixtures at once. **Any change must be checked against both**, because they fail in opposite directions.

- `pi/tools/fixtures/table-pen-scan.json` (branch) — a real scan from inside the pen. True size unknown.
- `data/runs/room-scan.ndjson` — the simulated room, 4210 × 5090 mm, carrying the true pose in **every** frame. This is what makes `pose.py` testable without hardware.

---

## 7. How to run everything

```
# fake robot + dashboard, no hardware at all
npm run fake                      # :8080, loops data/runs/room-scan.ndjson
npm run dash                      # :5173

# the brain, with a real lidar on the Mac
cd pi && SCOUT_ESP32_PORT=none SCOUT_CAMERA=none .venv/bin/python -m scout

# the brain, with a fake ESP32 on a pty and no lidar
cd pi && .venv/bin/python tools/fake_esp32.py     # prints the exact next command

# flash the ESP32 (PlatformIO lives in firmware/.venv; there is no pipx on this machine)
cd firmware && .venv/bin/pio run -t upload && .venv/bin/pio device monitor -b 115200
```

`git` on this machine is the Xcode shim and is **blocked by an unaccepted licence**. Use the Command Line Tools binary — no sudo needed:

```
/Library/Developer/CommandLineTools/usr/bin/git
```

---

## 8. What to do next, in order

1. **Measure the pen with a tape.** Unblocks §4. Five minutes.
2. **Plug the lidar back in**, restart the service, run `check_pose.py`.
3. **Decide the `pose-arena-fit` question** — merge, keep tuning, or run both fits and pick per scan. Needs the tape measurement first.
4. **Chase the motor driver and 5 V rail.** The only hard blocker for autonomy, unresolved all session.
5. **Flash the ESP32.** Zero risk, one USB cable, toolchain already cached.
6. **Set the Pi up** — hostname `scout`, hotspot, systemd. Removes the USB tether, which is what forced a laptop and a person into the arena and made the scans worse.
7. **Tell the `gaps.py` author** the IMU is gone and `main` is v2 (or point them at §3).
8. **Record a real run** once pose works, save to `data/runs/`, add to `runs/index.json`. Better demo insurance than the simulated file.

### If pose is never made to work in time

The demo degrades honestly rather than collapsing. **Clearance needs no position** — Scout still measures gaps and still fires width verdicts. What is lost is the map and placing findings on it. The replay still shows the full story, and `RUNBOOK.md` instructs the presenter to say it is a simulated room with scripted labels. **Do not let anyone narrate the replay as a recording of the robot.**

---

## 9. Judgement calls, so they are not silently reversed

- **Ramps are labelled, never judged.** Anything reintroducing a slope number is wrong.
- **`pose: false` is honoured everywhere.** Every gate in `pose.py` fails closed.
- **Clearance refuses to measure a blind side.** If a side of the slice is mostly no-returns, a near non-reflective surface may be hidden and the gap reads **too wide** — turning a barrier into a pass. That is the one error direction that matters for an accessibility tool.
- **`unverified` gaps never become measurements** — inherited from the `evidence` model, which was better than what this session first wrote.
- **A gap Scout cannot drive towards is not a passage.** Without that rule every corner fires a false `width_fail` (15 false positives on the simulated run, now 2).
- **The simulated run is a simulator, not a recording.** Rewritten this session because the old one emitted a `slope_fail` the robot can no longer produce, and `RUNBOOK.md` told the presenter to narrate it.
