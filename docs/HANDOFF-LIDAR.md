# Handoff — lidar, pose and the 2D map, 2026-09-19 21:00 EDT

Workstream handoff, same shape as `HANDOFF-REDBOARD.md`. **`docs/HANDOFF.md` is still the project
catch-up** — read its §1 and §2 first if you are new. This file covers only what the lidar and
geometry work established, including **the first live test with a real lidar and a real space.**

---

## 1. The headline: the lidar works and the live 2D view is correct

The USB adapter arrived and Scout saw a real room for the first time. **The live lidar view was
reported "mostly correct"** — walls straight, proportions right.

Two things looked like faults and **neither is one.** Both were chased to the source this session.

### "Phantom parallel lines" — that is the gap overlay, not a wall

`mission-control/src/LidarView.tsx:138-145` draws **every gap as a chord**: a straight line joining
the two edges of an opening.

```js
for (const gap of scan.gaps) {
  ...
  g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke();
```

In a space with a missing wall, that opening is one enormous gap, so a line is drawn **straight
across it, parallel to the wall opposite.** That is the "impossible" line. It is the app reporting
*"here is an opening, this wide"*, exactly as designed.

A second overlay reads the same way: `LidarView.tsx:97-105` draws **faint amber radial strokes**
along every bearing that got no return, deliberately, "so a blind arc is visible as blindness
rather than as empty space."

> **What is real geometry:** the teal outline and the dark dots.
> **What is annotation:** amber rays, and coloured chords (dashed = `unverified`, solid =
> `see_through`).

How to tell them apart on screen in two seconds: a gap chord **starts and ends exactly on a real
dot with nothing in between**; a real wall has dots along its whole length.

### The white flash — **still open, needs one observation**

The panel blanks to "no lidar" whenever `telem.lidar` is false or the scan is not 360 long
(`LidarView.tsx:46`). `lidar.py` sets `self.scan = []` and `connected = False` on any session
exception, so **a dropout-and-reconnect produces exactly this flash.**

**This was not confirmed before the session ended.** To settle it:

1. Watch the service terminal while the screen flashes.
2. If these appear, it is a real dropout and the error text names the cause:
   ```
   LIDAR /dev/cu.usbserial-XXXX: <the actual error>
   LIDAR DISCONNECTED: width off until it is back
   ```
3. **If nothing appears in the terminal, it is not a dropout** — look at the dashboard's WebSocket
   reconnect instead. Different bug, different fix.

Most likely causes, in order: **USB power** (the A2M8's motor draws real current — plug into the
Mac directly, never a hub), then cable quality (shows up as `capsule checksum ... does not match`),
then a genuine protocol desync.

**Ruled out already:** the express-scan decode in `rplidar.py:169-198` was audited line by line
against the SDK's `_onScanNodeCapsuleData`. Offset extraction, angle interpolation, the `<< 3`
per-sample increment and the 360° wrap are all faithful. **Do not go looking there.**

---

## 2. How the workflow actually works

This caused real confusion and is worth stating plainly. **It is continuous. There is nothing to
press to take a scan.**

There are **two different pictures** on the dashboard and they behave completely differently:

| | **LidarView** | **Map** (occupancy grid) |
| --- | --- | --- |
| Shows | only the **current** rotation | everything seen so far, accumulated |
| Updates | 10 Hz | 1 Hz |
| Memory | **none** — each frame replaces the last | builds up permanently |
| Needs pose? | **No** | **Yes** (`server.py:252`, `if self.pose.ok`) |
| Works in a non-rectangular space? | Yes | No |

The loop in `server.py:236-300`: the lidar thread overwrites the latest scan ~10×/s; every tick the
service fits the room rectangle to it; **only if the fit succeeds** does it fold that scan into the
grid; once a second it ships the whole grid to the dashboard. A failed fit leaves the grid
untouched and the live view carries on regardless.

**Mapping by hand works and needs no motors.** Position comes from the room's shape, not from
wheels, so carrying the lidar is equivalent to driving it — `server.py:239` explicitly anticipates
"a lidar someone is carrying across the room". Walk it slowly and level; each fix is independent,
so jerking it just gets scans rejected. This matters more now that autonomy was dropped
(`011d570`, `a62fb2a`) and Scout is driven by hand.

---

## 3. What a three-walled (U-shaped) space does

Tested in simulation, 4000 × 3000 mm with one long wall missing. **The answer depends entirely on
what is beyond the opening:**

| Beyond the open side | Fit | Verdict |
| --- | --- | --- |
| Nothing within lidar range | 4023 × 2961 mm, 100% on-edge | **correct** |
| A wall ~2 m beyond | 9291 × 5023 mm, 61% | ⚠️ **locks, wrong size** |
| A wall ~5 m beyond | 11693 × 8023 mm, 58% | ⚠️ **locks, wrong size** |
| A wall ~10 m beyond | 54% | no pose |

The good case works because the two side walls run the full depth, so their far ends imply the
missing corner. **The middle rows are the danger:** `pose` reads true, the dashboard looks healthy,
and the room is two to three times its real size.

**The single check that protects you: compare the locked room size to a tape measure.** Nothing
else distinguishes the good case from the bad one.

### Blocking the opening must be continuous

| Fourth wall | Result |
| --- | --- |
| bare U | locks, wrong size |
| 50% covered | no pose |
| 70% covered | no pose |
| **85% covered** | **no pose** |
| 100% covered | **correct** |

**Partial coverage is worse than none.** The convex hull is defined by the *furthest* returns, so
one gap is all it takes. A row of chairs with daylight between them will not work; it has to be
continuous at lidar height. Small mercy: partial coverage fails *safe* (no pose) rather than lying.

**Clearance needs no pose and works in a U.** Simulated slots of 700/860/900/1200 mm all measured
within ~24 mm and on the safe side (reads narrow, not wide). The ~24 mm is an artifact of how noise
was injected in simulation — **do not quote it**; the real figure is still 510 mm read as 504 mm.

---

## 4. Code changed this session

Four commits, all on `main`, all pushed: `b0bd255`, `c6a9710`, `dd21975`, `db7d658`.

- **`pose.py` — square rooms are announced.** `_warn_if_square()` logs `ROOM IS SQUARE (w × l)` at
  lock time. A square room has an **unfixable** failure: a 90° turn made while the fit is lost is
  indistinguishable from no turn (measured 90.2° out, versus 0.2° for an oblong room). See
  `HANDOFF.md` §4 for the full argument. **The mitigation is a tape measure, not code.**
- **`pose.py` — the relock path was unreachable** when every rotation was rejected outright, so
  Scout could lose pose *permanently* with no way back. Now it re-acquires and voids the map.
  Rotation maths pulled into `Pose._read_as`.
- **`pose.py` — scan-rate comments** corrected to the A2M8's 10 Hz. No tuned constant changed.
- **`tools/check_room.py` — new.** Casts real 360-ray scans in rooms built to order, wall-follows a
  lap, checks every pose against truth. Reports heading error **separately** from position error,
  because a frame flip is a different bug from drift. Seven shapes, all passing: **2.0 mm median,
  8.5 mm max, zero flips.**
- **`RUNBOOK.md` §0 — how to build the room**, before how to start the robot.

**Verification standard used, and worth keeping:** every `pose.py` change was diffed
**frame-by-frame** against `data/runs/room-scan.ndjson` — 660 frames, 0 differing — not merely
compared on summary statistics. A refactor that changes no behaviour should prove it.

---

## 5. What to do next

1. **Settle the white flash.** §1 above — one observation of the service terminal decides it.
2. **Find a closed, oblong room** and check the map actually accumulates. This is the one capability
   never yet demonstrated end to end. Oblong, closed corners, clear floor (`RUNBOOK.md` §0).
3. **Tape-measure whatever room you use** and compare to `room frame locked: W x L mm`. If they
   disagree, believe the tape and do not trust the map.
4. **Hand-carry a full lap** and record it (`tools/record.py`) — a real run is better demo insurance
   than the simulated file.
5. If the map never works in time: **clearance needs no pose** and still fires width verdicts. The
   demo degrades honestly. Do not let anyone narrate the simulated replay as a recording.

---

## 6. Tools

```
cd pi && .venv/bin/python tools/check_room.py      # pose in simulated rooms of any shape — no hardware
cd pi && .venv/bin/python tools/check_audit.py     # 17 clearance and gap checks — no hardware
cd pi && .venv/bin/python tools/check_pose.py      # LIVE: walks every pose gate, saves tools/scan-dump.json
cd pi && .venv/bin/python tools/read_scan.py       # LIVE: says in words what clearance() sees (teammate's)
```

**Any change to `pose.py` must pass `check_room.py` *and* be diffed against the recorded run.** The
two fail in opposite directions — that is the whole reason both exist.

`scan-dump.json` is how a scan gets handed to someone who is not standing in the room. If anything
looks wrong in the live view, capture it and attach it rather than describing it.

---

## 7. Do not redo these

- **Do not re-add a heading gate to stop 90° flips.** Written, tested, removed. It is aimed the
  wrong way: the deceptive candidate has `dh ≈ 0` and the truthful one `dh ≈ 90`, so the gate
  rejects the truth. Error was 90.2° with it on *or* off.
- **Do not merge `pose-arena-fit`.** Superseded. Keep the branch only for
  `pi/tools/fixtures/table-pen-scan.json`, the only real lidar scan the project has.
- **Do not replace the hull fitter**, and **do not infer a clip radius per scan** (max error
  2400 mm). `HANDOFF.md` §5 lists nine approaches that failed and why.
- **Do not hunt for bugs in the express decode.** Audited against the SDK this session.
