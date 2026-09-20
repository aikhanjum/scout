# Handoff, 2026-09-19 14:40 EDT

> **Updated later the same day: the ESP32 never arrived and a SparkFun RedBoard took its place.**
> It and its DK Electronics shield drive all four motors and are working on the bench. The Pi
> service was adapted to the board's own serial dialect in `pi/scout/redboard.py`; `docs/PROTOCOL.md`
> section 9 describes what is fitted, and section 2 and the status table below are corrected.
> **The board has no buzzer and no LEDs**, so a width verdict is now spoken and shown but neither
> beeped nor lit. The firmware bench notes are in `docs/HANDOFF-REDBOARD.md`, whose §7 and §8 are
> annotated as superseded: there is still no IMU, the lidar was not denied, and networking is the
> phone hotspot.

Submission is **Sunday 2026-09-20 08:00 EDT** — roughly **17 hours** from this timestamp.

Two audiences. **Sections 1–3 are for a teammate** who has been heads-down on something else and needs to know what Scout is and where it stands. **Sections 4 onward are the working detail** for whoever picks up the code next.

`CLAUDE.md` has the rules, `docs/PROTOCOL.md` is the contract, `RUNBOOK.md` is demo day.

> **Later the same day, the lidar met a real room for the first time and the live 2D view was
> correct.** Two things that looked like faults were not: the "phantom" lines are the gap-chord
> overlay, and the white flash is most likely a USB dropout (unconfirmed — one observation settles
> it). The workflow is continuous; nothing triggers a scan; the live view needs no pose but the
> accumulating map does. **`docs/HANDOFF-LIDAR.md` has all of it**, plus what a three-walled space
> does and why partial coverage of a missing wall is worse than none.

---

## 1. Read this first

### There is no IMU, and there never will be

Confirmed by the team. That is not a missing part, it is a change of what Scout can claim. A ramp meeting a horizontal lidar plane returns exactly what a wall returns — same points, same shape. Nothing on Scout measures slope. So:

> **Ramps are labelled by the camera and never judged. Clearance width is the only building-code verdict Scout gives.**

The old demo beat — *"Ramp too steep. 7.1 degrees."* — **is gone and cannot come back.** If you are still building or pitching against slope, stop.

### What Scout is

A Roomba that draws an accessibility map. It drives itself around a room, maps it in 2D, and marks what it finds.

```
RedBoard (motors, watchdog)  <--USB-->  Pi 4 (lidar, camera, map, audit)  <--wifi-->  dashboard
```

The division of labour is the whole design:

| | Job | Produces |
| --- | --- | --- |
| **Lidar** | **Geometry.** Where walls are, where Scout is, where obstacles are, how wide gaps are. | the map, the pose, the clearance verdicts |
| **Camera** | **Names.** What a given obstacle is, including whether it is a ramp. | `label`, `confidence`, a photo |

The camera never measures anything and never decides a pass/fail. Its only job is turning *"an obstacle at (2400, 4500)"* into *"a ramp at (2400, 4500)"*.

### How Scout gets a position without odometry or an IMU

Nothing on the robot knows how far it has driven. Position comes out of the lidar: **fit the rectangle of the room in every scan and read position and heading straight off it.** Nothing accumulates, so nothing drifts — but it fails outright in spaces that are not closed rectangles, and then `pose` goes false and the map stops growing until it recovers.

**If you consume telemetry: honour `pose: false`.** When false, `x_mm`/`y_mm`/`heading_deg` are zero and mean nothing.

### ⚠️ The room is not scenery, it is the sensor

This is the single most important operational fact in the project, and it is new since the last handoff. Because pose is read off the room's rectangle, **how you build the room decides whether Scout works at all.** Three rules, in the order they bite:

1. **Oblong, not square.** One side at least 300 mm longer than the other. See §4 — in a square room a quarter turn is indistinguishable from no turn, and that is not fixable in software.
2. **Closed corners.** No gaps. A gap lets the beam out and the fit is built from whatever it found in the space beyond.
3. **Clear floor.** Feet, bags and standing people hide walls.

`RUNBOOK.md` §0 is this list in demo-day form.

### The demo

1. Put Scout on the floor, press **ROAM**.
2. It finds the walls and starts following the right-hand wall.
3. The map draws itself on the big screen within a lap.
4. It stops at an obstacle, photographs it, names it, drops a labelled marker, goes around.
5. It squeezes through a narrow gap and calls it: red light, two beeps, *"Too narrow between a wall and an obstacle. 51 centimetres. A wheelchair needs 86."*

---

## 2. Status at a glance

| | State |
| --- | --- |
| Protocol v2, Pi brain, firmware, dashboard, simulator | **Done and pushed.** 33 commits on `main`. |
| Pose / map / clearance / wall following | **Validated in simulation across 7 room shapes.** Never yet run in a real closed room. |
| Lidar | **Works.** Adapter obtained — **not yet tested with it.** Confirmed on hand; the reservation was not denied. |
| Motor board + firmware | **Working on the bench.** SparkFun RedBoard, `06_serial_drive.ino`, all four motors under control, watchdog verified. The ESP32 firmware in `firmware/` is superseded and is not flashed to anything. |
| Camera / CLIP labels | **Never executed.** No Pi, no camera, no model files. Degrades to `"unknown"`. |
| Raspberry Pi | **Flashed, on the hotspot, service runs by hand and finds its devices.** systemd unit and the dashboard end-to-end check still to do. |
| Motor driver | **Exists and works.** DK Electronics shield (2x L293D + SN74HC595) on the RedBoard. |
| Beep and indicator LEDs | **Gone with the ESP32.** The spec wants a light, a beep and a spoken verdict; only the last two channels exist. D2/D3 are free on the shield if a piezo is wanted. |
| 5 V rail | **Does not exist.** 18650s cannot feed a Pi 4. |
| The demo room | **Not built yet.** Must be oblong and closed — see §4. |

Numbers that are real, all from simulation: room size within 10 mm, position **2.0 mm median / 8.5 mm worst** across seven room shapes, heading within 0.5°, on 99–100% of scans; a 510 mm slot read as **504 mm**; 3 ms per scan.

**The honest summary: the software is finished and validated in simulation; the robot does not exist yet and the room has not been built.**

---

## 3. What this means for your workstream

### If you wrote `gaps.py` / `LidarView.tsx` / the RPLIDAR driver

**Your work survived intact.** Nothing was deleted:

- `rplidar.py` — kept wholesale. You also corrected it to the A2M8 (commit `a00c743`); that rename is on `main`.
- `gaps.py` — kept and wired in. It feeds both the lidar view *and* the width audit.
- `LidarView.tsx` — kept, rebuilt to read `telem.scan` / `telem.gaps`.
- `check_audit.py` — **all 17 checks pass.**

**Your `evidence` model is load-bearing.** A gap bounded by an arc of no-returns can never become a measurement, because a doorway and a non-reflective surface are identical in one rotation. The same idea now guards the clearance measurement.

**What changed:** v1.2's separate 2 Hz `scan` frame is folded into `telem` as a fixed 360-int array. `proto` is now `2` — a v1 consumer and a v2 Scout will not interoperate.

### If you are on hardware

The motor driver is **no longer open.** A SparkFun RedBoard with a DK Electronics shield (2x L293D behind an SN74HC595) drives all four DAGU motors, ganged as two sides, left pair on one channel and right pair on the other. Keep the shield's PWR jumper **removed**, and never put the motor pack on the RedBoard's 5 V pin or barrel jack.

One thing still blocks the *driving* half of the demo, open for three sessions:

1. **5 V regulation.** 18650s are 7.4–8.4 V; a Pi 4 needs a real 5 V at 3 A. Target a 5 A buck — **not an LM2596**, whose "3 A" is a peak figure that sags into brownout.

*Shortcut that removes it:* put the Pi and lidar on a **USB power bank**, give the 18650s to the motors alone.

**Know this failure mode:** an undersized 5 V rail does not crash the Pi cleanly. It browns out the USB ports first, so the **lidar drops at random and it reads as a software bug**. `vcgencmd get_throttled` → `0x0` healthy, bit 0 = under-voltage now, bit 16 = since boot. Check before blaming code.

**Mounting:** the lidar must be level, at the top, with nothing of the robot above its beam plane. Anything poking up becomes a permanent wall in every scan and the room fit dies.

**Building the room is now a hardware task with a correctness requirement** — see §1 and §4. Oblong, closed, clear.

### If you are on the dashboard

The slope gauge and the GLB viewer are gone. The 2D map canvas is the screen (plain canvas, no three.js). `LidarView` sits beneath it. Clearance shows `--` rather than a number when there is nothing to report — normal in open space and when a side is too blind to trust.

### If you are on the pitch

Say these out loud rather than letting a judge find them:

- Scout labels ramps and never judges them, because it cannot measure slope.
- Widths are Scout's own lidar measurements — quote the error.
- The replay is a **simulated room with scripted labels**, not a recording of the robot.
- Two rules out of many; a screening tool for a human inspector, not a legal inspection. Not tested with wheelchair users.

---

## 4. Localisation: what the last session settled

The previous handoff left this as the open critical path. It is now resolved, and the resolution is **a decision about the room, not a change to the fitter.**

### The table pen failed, and why

The old arena was **four tables on their sides** in an open lab. `pose.py` fits the convex hull of a scan and takes its minimum-area rectangle. In the pen that produced a **12 480 × 7 539 mm** rectangle with **3% of returns on it** and never locked.

Cause: the tables were originally set to a tight square, then **moved apart to enlarge the arena, which opened a diagonal slot at every corner.** 33% of bearings came back empty and **43% of returns were beams escaping into the lab**, some out to 9 m. The hull is built from exactly those escapees.

### The pen's size, which nobody had measured

The last handoff said this was blocking and that two fit variants disagreed (**2280 × 2760** and **1462 × 1565 mm**). It was settled from the scan itself — read the four perpendicular minima straight off `table-pen-scan.json`:

| bearing | range | wall |
| --- | --- | --- |
| 32° | 1212 mm | front |
| 122° | 976 mm | left |
| 214° | 970 mm | rear |
| 306° | 1217 mm | right |

90°, 92°, 92°, 86° apart — a clean rectangle. Opposite pairs sum to **2182** and **2193 mm**. An independent orientation sweep agrees at **2205 × 2212 mm, 66% on-edge**. Three further methods land within 60 mm.

**The pen was ~2.2 m square. Both earlier numbers were wrong.**

### The fix is to close the room, not to change the fitter

Measured on the real pen scan:

| | pen | simulated closed room |
| --- | --- | --- |
| hull fit (`main`) | 3% on-edge, **never locks** | 1.6 mm median, 88%, 0 relocks |
| replace fitter with a wall sweep | 2205 × 2212, 66% ✓ | only 28/66 frames within 200 mm ✗ |
| *adaptive* clip radius | locks ✓ | 78% poses, **max error 2400 mm** ✗ |
| fixed clip at 1800 mm | 2227 × 2265, **98%, locks** ✓ | untouched (clip off) ✓ |
| **block the gaps physically** | 2283 × 2247, **99%, locks** ✓ | untouched ✓ |

**Closing the room wins outright**, costs no code, and also fixes two things a clip does not: the occupancy map stops filling with lab returns, and `clearance()` starts measuring. In the open pen the right-hand side of the slice was 74% no-returns, over the 60% dropout limit, so Scout **correctly refused to measure** — meaning the headline width verdict would not fire in the one place there was a gap.

### 🚨 The new finding: a square room cannot be fixed

Separate from the gaps, and more dangerous because it is silent.

Matching a fit back to the locked frame leans on the room's **shape**: stand a 4 × 5 m room on its side and the extents stop matching, so the wrong reading is rejected. **When both sides are equal that cue is gone.** If Scout then turns roughly 90° while it cannot see — someone leans over the lidar mid-corner — the scan that comes back is *identical* to the one it would have produced had it never turned.

Measured with `tools/check_room.py`:

| | comes back |
| --- | --- |
| square 2200 × 2200 | **90.2° out** |
| oblong 4210 × 5090 | 0.2° out |

**No algorithm fixes this.** The deceptive reading is the one that looks *more* continuous, not less — so any heading gate rejects the truth and admits the lie. Furniture does not help either: pose is read off the fitted rectangle alone, not its contents.

The mitigation is a tape measure. **Make one side 300 mm longer than the other** and the ambiguity disappears. `pose.py` logs `ROOM IS SQUARE (w × l)` at lock time if you got it wrong.

### `pose-arena-fit` is superseded — do not merge it

The branch replaced the fitter to cope with the open pen. With the pen abandoned it is the wrong trade (it regressed the closed room from 1.6 mm to ~100 mm). **Keep the branch only for `pi/tools/fixtures/table-pen-scan.json`**, which is the only real RPLIDAR scan the project has.

---

## 5. Approaches that failed — do not repeat them

**On fitting the room:**

1. **Plain point counts** → picks whatever is nearest. A lidar samples at a fixed angular step, so a surface returns points in inverse proportion to distance. A chair at 1.4 m beat the room wall at 3.9 m.
2. **Range-weighted counts alone** → overcorrects. Two strays 9 m away outvote a real wall.
3. **Scoring a wall by how far its returns run along it** → every bin on one axis picks up the two *parallel* walls crossing it, so every bin scores the full room length.
4. **Quantile-trimmed extremes (2%)** → deletes sparse walls. A ramp against the far wall leaves it **eleven** returns; trimming seven removes the wall.
5. **Maximising on-edge fraction alone** → the *true* rectangle scores 83%, a wrong one cutting across a ramp scores **84%**.
6. **Requiring the rectangle to contain the scan** → right for a closed room, wrong for an open pen.
7. **Angular contiguity as a wall test** → fails in an open arena, because the lab surfaces seen through the gaps are *genuinely* contiguous.
8. **Choosing a clip radius per scan** → carves a high-scoring sub-rectangle out of near furniture. Max error 2400 mm.

**On the square room:**

9. **A heading gate to block 90° flips** → written, tested, **removed**. It is aimed the wrong way: the dangerous candidate has `dh ≈ 0` and the truthful one has `dh ≈ 90`, so the gate rejects the truth. Error was 90.2° with the gate on *or* off. Do not re-add it; build the room oblong instead.

Two constraints that helped and should be kept: **walls must straddle the lidar**, and **ties go to the larger rectangle**.

---

## 6. Tools and fixtures

```
cd pi && .venv/bin/python tools/check_room.py      # pose in simulated rooms of any shape — no hardware
cd pi && .venv/bin/python tools/check_audit.py     # 17 clearance and gap checks — no hardware
cd pi && .venv/bin/python tools/check_pose.py      # why is there no pose, on a LIVE Scout
```

- **`check_room.py`** (new) casts real 360-ray scans in rooms built to order, wall-follows a lap, and checks every pose against the truth it came from. Reports heading error **separately** from position error, because a frame flip is a different bug from drift. Seven shapes plus the blind-turn case that demonstrates the square-room limitation as an executable check.
- **`check_pose.py`** reads one real scan over the WebSocket (the lidar port is held exclusively by the service) and walks it through every gate in `pose.py`. It saves the scan to `pi/tools/scan-dump.json` — **that dump is how a scan gets handed to someone not standing in the room.**
- `data/runs/room-scan.ndjson` — the simulated room, 4210 × 5090 mm, true pose in **every** frame.
- `pi/tools/fixtures/table-pen-scan.json` (on `pose-arena-fit`) — the only real lidar scan we have.

**Any change to `pose.py` must be checked against `check_room.py` *and* the recorded run.** The recorded-run check should be frame-for-frame identical unless you meant to change behaviour.

---

## 7. How to run everything

```
# fake robot + dashboard, no hardware at all
npm run fake                      # :8080, loops data/runs/room-scan.ndjson
npm run dash                      # :5173

# the brain, with a real lidar on the Mac
cd pi && SCOUT_MOTOR_PORT=none SCOUT_CAMERA=none .venv/bin/python -m scout

# the brain, with a fake motor board on a pty and no lidar
cd pi && .venv/bin/python tools/fake_redboard.py  # prints the exact next command

# the motor board is flashed from the Arduino IDE (06_serial_drive.ino), not from this repo.
# firmware/ holds the ESP32 bridge that was never used; nothing flashes it.
```

`git` on this machine is the Xcode shim and is **blocked by an unaccepted licence**. Use the Command Line Tools binary — no sudo:

```
/Library/Developer/CommandLineTools/usr/bin/git
```

Environment: `SCOUT_ESP32_PORT`, `SCOUT_LIDAR_PORT` (`none` disables, or a device path), `SCOUT_CAMERA`, `SCOUT_LIDAR_OFFSET_DEG`, `SCOUT_PORT`.

---

## 8. What to do next, in order

The adapter has arrived, so the lidar is testable again. **Do Stage A before building the room** — otherwise a failure could be either the driver or the room and you will not know which.

### Stage A — is the lidar alive? (~10 min, anywhere)

1. Plug it in. `ls /dev/cu.*` must show a new entry. **Nothing new = a macOS driver problem** (CH340 or CP2102 depending on the adapter), not a Scout problem.
2. `cd pi && SCOUT_MOTOR_PORT=none SCOUT_CAMERA=none .venv/bin/python -m scout`
   Want: `LIDAR connected on /dev/cu.… health GOOD`.
   `LIDAR NOT FOUND` → it lists every port it saw; force one with `SCOUT_LIDAR_PORT=`.
   `health ERROR` or repeated drops → **power**. Plug into the Mac directly, not a hub.
3. Second terminal: `cd pi && .venv/bin/python tools/check_pose.py`.
   **On a desk it will say `NO POSE`. That is correct — ignore it.** You only want **200+ returns** and a sane min/median/max.

### Stage B — does the room work?

4. **Build the room: oblong (one side 300 mm+ longer), closed corners, clear floor.** `RUNBOOK.md` §0.
5. Lidar in the middle, service running. Watch for `room frame locked: W x L mm`. **If `ROOM IS SQUARE` appears, move a wall now.**
6. `check_pose.py` again — check 4 (*is it a rectangle?*) wants **≥55%**; a closed room reads **95%+**. Below 55% is usually an open corner.
7. Compare the locked size to a tape measure. If they disagree, **believe the tape** — the fit latched onto something that is not your wall.

### Then

8. **Chase the 5 V rail.** The motor driver is done; this is the last hardware blocker. A USB
   power bank for the Pi and lidar removes it, leaving the cells to the motors alone.
9. **Finish the Pi**: the systemd unit (`sudo cp pi/scout.service /etc/systemd/system/ &&
   sudo systemctl enable --now scout`), then the reboot test and the dashboard end-to-end check.
   Remove `brltty` first — it claims the RedBoard's CH340 and the port vanishes under you.
10. **Decide on the beep.** A width verdict currently has one channel, not three. A piezo on the
   shield's free D2 or D3 would return it; it is a firmware change, not a Pi one.
11. **Record a real run** once pose works; save to `data/runs/`, add to `runs/index.json`. Better demo insurance than the simulated file.

### If pose never works in time

The demo degrades honestly rather than collapsing. **Clearance needs no position** — Scout still measures gaps and still fires width verdicts. What is lost is the map and placing findings on it. `RUNBOOK.md` instructs the presenter to say the replay is a simulated room with scripted labels. **Do not let anyone narrate the replay as a recording of the robot.**

---

## 9. Judgement calls, so they are not silently reversed

- **Ramps are labelled, never judged.** Anything reintroducing a slope number is wrong.
- **Build the room oblong.** Not a preference — a square room has an unfixable failure (§4).
- **`pose: false` is honoured everywhere.** Every gate in `pose.py` fails closed.
- **Clearance refuses to measure a blind side.** If a side of the slice is mostly no-returns, a near non-reflective surface may be hidden and the gap reads **too wide** — turning a barrier into a pass. The one error direction that matters for an accessibility tool.
- **`unverified` gaps never become measurements.**
- **A gap Scout cannot drive towards is not a passage.** Without that rule every corner fires a false `width_fail` (15 false positives on the simulated run, now 2).
- **The simulated run is a simulator, not a recording.**
- **`pose.py` re-acquires rather than going permanently blind.** The relock path used to be unreachable when every rotation was rejected outright; Scout could lose pose forever with no way back.
