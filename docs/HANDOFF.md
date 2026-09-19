# Handoff, 2026-09-19 07:15 EDT

Written at the end of a session that ran out of budget mid-problem. Submission is **Sunday 2026-09-20 08:00 EDT**, so roughly **25 hours** remain from this timestamp.

Read `CLAUDE.md` for the rules and `docs/PROTOCOL.md` for the contract. This file is only what a new session cannot reconstruct from the repo.

---

## 1. What changed this session

Scout was respecified. **There is no IMU and there never will be** — the team confirmed it. A ramp meeting a horizontal lidar plane is indistinguishable from a wall, so slope cannot be measured at all.

The consequence, which must not be quietly undone: **ramps are labelled by the camera and never judged. Clearance width is the only building-code verdict Scout gives.**

`main` is now protocol **v2**: no IMU, a 360-entry `scan` in telemetry, a room frame fitted from the lidar, an occupancy map, `wall_follow`, and `obstacle`/`ramp` events carrying a camera label. 14 commits, all pushed.

Teammates' parallel work (protocol v1.2/v1.3, `gaps.py`, `LidarView.tsx`, express scan in `rplidar.py`, `RUNBOOK.md`, `check_audit.py`) was **merged, not overwritten** — every file survives and is wired in. All 17 of their `check_audit.py` checks pass against v2.

**Nobody has told the teammate who wrote `gaps.py` that `main` is now v2 and the IMU is gone.** That conversation is still owed.

---

## 2. The live problem: Scout cannot localise in the arena the team built

This is where the session stopped. It is the critical path.

### What happened

The team's test arena is **four tables laid on their sides**, forming a pen, inside an open lab. Not a room.

`pi/scout/pose.py` on `main` fits the **convex hull** of a scan and takes its minimum-area rectangle. That works beautifully in a closed room and **fails completely in the pen**: the hull is defined by the most distant returns, and in an open arena those are whatever escaped through the gaps between tables. On a real scan from inside the pen it produced a 12.5 × 7.5 m rectangle with **3%** of returns on it, and never produced a pose.

The room *is* in the data. RANSAC on that same scan finds four real walls — two parallel pairs **86° apart**, the longest 7.6 m with 18% of the scan on it. The hull method simply cannot see them.

### What was built (parked, not merged)

Branch **`pose-arena-fit`**, pushed. `main` is untouched and clean.

It sweeps orientation and reads the two opposite walls off the projection histogram **two ways** — trimmed extremes, and range-weighted peaks — keeping whichever puts more of the scan on the resulting rectangle.

Measured:

| | Table pen (real scan) | Simulated closed room |
| --- | --- | --- |
| hull fit (on `main`) | 3% on walls, **never locks** | 1.6 mm median, 88% of scans, 0 relocks |
| branch fit | **76% on walls, locks** | ~100 mm median, 83% of scans, 1 relock |

**It fixes the real environment and makes the clean-room case markedly worse.** That trade was never accepted, which is why it is on a branch.

### Why each attempt failed — do not repeat these

Six approaches were tried. Each failed for a reason worth knowing:

1. **Plain point counts** → picks whatever is nearest. A lidar samples at a fixed angular step, so a surface returns points in inverse proportion to distance. A chair at 1.4 m beat the room wall at 3.9 m.
2. **Range-weighted counts alone** → overcorrects. Two strays 9 m down a corridor outvote a real wall.
3. **Scoring a wall by how far its returns run along it** → cannot discriminate. Every bin on one axis picks up the two *parallel* walls crossing it, so every bin scores the full room length.
4. **Quantile-trimmed extremes (2%)** → deletes sparse walls. In the simulated room a ramp stands against the far wall leaving it **eleven** returns; trimming 7 removes the wall.
5. **Maximising on-edge fraction alone** → genuinely ambiguous. The *true* rectangle scores 83%; a wrong one cutting across the ramp's face scores **84%**. Hence the area tie-break ("a room contains its furniture").
6. **Requiring the rectangle to contain the scan** → correct for a closed room, wrong for the pen, where 43% of returns legitimately escape through the gaps.

Two constraints that did help and should be kept: **walls must straddle the lidar** (it sits inside the room), and **ties go to the larger rectangle**.

### The one measurement that unblocks this

**Nobody has measured the pen with a tape.** Two variants of the fit gave **2280 × 2760 mm** and **1462 × 1565 mm** for the same scan. They cannot both be right, and without ground truth the 76% score may be flattering a wrong answer.

**Do this before touching the algorithm again.** Then run `cd pi && .venv/bin/python tools/check_fit.py` (on the branch) and see which matches.

A separate concern worth raising with the team: a pen 1.5–2.5 m across, against an 860 mm clearance limit, has room for about one gap and almost no driving. It is fine for proving pose and clearance. It is a thin demo arena. Bigger tables or a small real room would be better.

---

## 3. Tools built for this, and how to use them

Both are on `main` unless noted.

```
cd pi && .venv/bin/python tools/check_pose.py      # why is there no pose, on a LIVE Scout
cd pi && .venv/bin/python tools/check_fit.py       # branch only: score the fit on both fixtures
cd pi && .venv/bin/python tools/check_audit.py     # 17 clearance and gap checks
```

`check_pose.py` reads one real scan over the WebSocket (the lidar port is held exclusively by the service) and walks it through every gate in `pose.py`, printing what each saw and wanted. It saves the scan to `pi/tools/scan-dump.json` — **that dump is how a scan gets handed to a new session.**

`check_fit.py` (branch) scores any change to `pose.py` against both fixtures at once. **Any change must be checked against both**, because they fail in opposite directions.

Fixtures:
- `pi/tools/fixtures/table-pen-scan.json` (branch) — a real scan from inside the pen. True size unknown.
- `data/runs/room-scan.ndjson` — the simulated room, 4210 × 5090 mm, with the true pose in **every** frame. This is what makes `pose.py` testable without hardware.

---

## 4. State of the hardware

| Item | State |
| --- | --- |
| RPLIDAR A1 | Works. Spins, reports, express mode. **Was unplugged at session end** — `/dev/cu.usbserial-0001` is gone. |
| Raspberry Pi 4 | Never used. Not flashed, no hotspot, no systemd. **Not a blocker for anything.** |
| ESP32 | Firmware **compiles clean** (RAM 6.6%, flash 23.2%, no warnings). Never flashed to a board. |
| Motor driver | **DOES NOT EXIST.** Four DAGU motors, nothing to drive them. Blocks driving only. |
| 5 V rail | **DOES NOT EXIST.** 18650s cannot feed a Pi 4. See `hardware/PINMAP.md`. |
| Pi camera | Never connected. CLIP path never executed. |

**PlatformIO is installed in `firmware/.venv`** (there is no `pipx` on this machine), and the Xtensa toolchain is cached, so flashing is now one USB cable away:

```
cd firmware && .venv/bin/pio run -t upload && .venv/bin/pio device monitor -b 115200
```

### The power failure mode to know

An undersized 5 V rail does not crash the Pi cleanly. It browns out the USB ports first, so **the lidar drops out at random and it reads as a software bug**. `vcgencmd get_throttled` returns `0x0` when healthy; bit 0 = under-voltage now, bit 16 = it happened since boot. Check it before blaming code. Common LM2596 buck modules are the wrong part — their "3 A" is peak. Use a 5 A buck, or put the Pi and lidar on a USB power bank and give the 18650s to the motors alone.

---

## 5. What is untested, and how badly

Be honest about this in the pitch. Nothing below has ever touched a robot.

- **Pose, mapping, clearance, wall following** — validated only against a simulated room. The one real-world contact (the pen) **failed**, which is section 2.
- **Camera / CLIP** — never executed. No Pi, no camera, no model files. Degrades to `label: "unknown"`, which is exercised.
- **Firmware** — compiles; never run on a board. Motors never turned.
- **Anything involving wheels** — blocked on the motor driver.

Numbers that *are* real, all from the simulated fixture: room size within 10 mm, position 4.5 mm median / 14 mm worst, heading within 0.2°, on 88% of scans; a 510 mm slot read as 504 mm; 3 ms per scan.

---

## 6. How to run everything

```
# fake robot + dashboard, no hardware at all
npm run fake                      # :8080, loops data/runs/room-scan.ndjson
npm run dash                      # :5173

# the brain, with a real lidar on the Mac
cd pi && SCOUT_ESP32_PORT=none SCOUT_CAMERA=none .venv/bin/python -m scout

# the brain, with a fake ESP32 on a pty and no lidar
cd pi && .venv/bin/python tools/fake_esp32.py     # prints the exact next command
```

`git` on this machine is the Xcode shim and is **blocked by an unaccepted licence**. Use the Command Line Tools binary instead — no sudo needed:

```
/Library/Developer/CommandLineTools/usr/bin/git
```

---

## 7. What to do next, in order

1. **Measure the pen with a tape.** Unblocks section 2. Five minutes.
2. **Plug the lidar back in**, restart the service, run `check_pose.py`.
3. **Decide the `pose-arena-fit` question** — merge, keep tuning, or run both fits and pick per scan. Needs the tape measurement first.
4. **Chase the motor driver and 5 V rail.** Still the only hard blocker for autonomy, and it has been unresolved all session.
5. **Flash the ESP32.** Zero-risk, needs only a USB cable, and the build is cached.
6. **Set the Pi up** — hostname `scout`, hotspot, systemd. Removes the USB tether, which is what forced the laptop and a person into the arena and made the scans worse.
7. **Tell the `gaps.py` author** the IMU is gone and `main` is v2.
8. **Record a real run** once pose works, save it to `data/runs/`, add it to `runs/index.json`. That is the demo insurance, and it is better than the simulated file.

### If pose is never made to work in time

The demo degrades honestly rather than collapsing. Without a pose, Scout still measures clearance and still fires width verdicts — those need no position. What is lost is the map and placing findings on it. The replay of the simulated run still shows the full story, and `RUNBOOK.md` already instructs the presenter to say out loud that it is a simulated room with scripted labels. **Do not let anyone narrate the replay as a recording of the robot.**

---

## 8. Judgement calls made this session, so they are not silently reversed

- **Ramps are labelled, never judged.** Anything that reintroduces a slope number is wrong.
- **`pose: false` is honoured everywhere.** A confidently wrong pose corrupts the map for the rest of a run; a missing one costs nothing. Every gate in `pose.py` fails closed.
- **Clearance refuses to measure a blind side.** If a side of the slice is mostly no-returns, a near non-reflective surface may be hidden and the gap would read **too wide** — turning a barrier into a pass. That is the one error direction that matters for an accessibility tool.
- **`unverified` gaps never become measurements**, inherited from the teammate's `evidence` model, which is better than what this session first wrote.
- **A gap Scout cannot drive towards is not a passage.** Without that rule every corner of every room fires a false `width_fail` (it was 15 false positives on the simulated run, now 2).
- **The simulated run is a simulator, not a recording.** It was rewritten this session specifically because the old one emitted a `slope_fail` the robot can no longer produce, and `RUNBOOK.md` told the presenter to narrate it.
