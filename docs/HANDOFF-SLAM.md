# Handoff — SLAM, 2026-09-19 21:42 EDT

Workstream handoff, same shape as `HANDOFF-LIDAR.md` and `HANDOFF-REDBOARD.md`. **`docs/HANDOFF.md`
is still the project catch-up** — read its §1 and §2 first if you are new. This file covers one
change and its consequences.

Submission is **Sunday 2026-09-20 08:00 EDT** — about **10 hours** from this timestamp.

---

## 1. The headline: position no longer needs a rectangular room

**`SCOUT_POSE=slam` is now the default.** Scout's position comes from matching each scan against
the map built so far, instead of from fitting the room's rectangle in a single scan.

That removes the constraint that has shaped this project for two days. Scout is no longer confined
to one closed rectangular room: a corridor, an L, or two rooms through a doorway all work now.

**It is not a free win. Read §2 before you quote a number at anybody.**

The old fitter is intact, validated, and one environment variable away:

```
cd pi && SCOUT_POSE=rect .venv/bin/python -m scout
```

If slam misbehaves on the day, that line is the whole rollback. `git tag rect-pose-validated`
(commit `123f5b3`) is the tree as it stood before any of this landed.

---

## 2. The trade, which is real

| | `rect` — `pi/scout/pose.py` | `slam` — `pi/scout/slam.py` **(default)** |
| --- | --- | --- |
| Where position comes from | the min-area rectangle of one scan's convex hull | matching this scan into the map so far |
| State | none. Every scan solved from scratch | accumulates |
| **Drift** | **none, ever** | **yes, and it compounds** |
| Works in | one closed rectangular room | any shape |
| Error | **2.0 mm median**, 8.5 mm worst, 7 room shapes | **18 mm median**, 35 mm worst, one recorded run |
| Reports `room` | `{w_mm, l_mm}` | `null` |

Measured by `pi/tools/check_slam.py` against `data/runs/room-scan.ndjson`, which carries the true
pose in every one of its 660 frames:

```
pose on 659/660 scans (100%), 0 frame restarts
position error   median  18 mm   p95  29 mm   max  35 mm
heading error    median 0.0 deg  p95 0.5 deg  max  1.0 deg
match score      median 1.00     min  0.89
5.2 ms per scan  (budget is 100 ms at 10 Hz; a Pi 4 is some 3x slower than this Mac)

DRIFT AFTER THE FULL RUN: 17 mm, 0.0 deg
```

### 🚨 Do not quote 17 mm, and do not quote 2.0 mm

Both numbers are true and both are the wrong number to say out loud.

- **2.0 mm belongs to `pose.py`**, which is no longer what is running. Saying it while slam is the
  engine is a false claim about a measurement.
- **17 mm came from a *simulated* room** — perfect rectangle, sharp corners, the modest range noise
  `gen.js` injects. It is the easy case by construction. A real room with furniture, grazing
  incidence and a lidar someone is carrying unevenly will be worse, and nobody knows by how much.

`check_slam.py` prints the drift on every run and the last line says so. **Run it against a real
recorded lap and quote that.** Rule 7.

### There is no loop closure

Drift compounds because each match is a fraction of a cell out and the next scan is matched against
a map that already carries that error. The fix for that is **loop closure** — recognising you are
back somewhere you have been and retroactively correcting the whole run. It is not implemented, it
is not going to be before submission, and **Hector SLAM does not have it either**, so this is a
normal place to stand, not a broken one. It is listed in `docs/LATER.md` as still cut.

---

## 3. How it works, in plain terms

Think of stitching a panorama. Take a scan, slide and rotate it over the map until the overlapping
parts line up, and *where it had to go* is where you are. Then paint it on and repeat, ten times a
second.

Concretely, `slam.py`:

1. Keeps a **likelihood field** — the map, blurred. Each return stamps a 5×5 kernel that fades
   outwards. The fade is the point: without it the field is a bed of spikes, a pose 30 mm out
   scores zero, and there is nothing for the search to climb.
2. For every candidate `(dx, dy, dtheta)` in a bounded window, sums the field under the scan's 180
   points and keeps the best. **Coarse at 100 mm cells first, then fine at 50 mm around the winner.**
3. Folds the scan into the field only after Scout has moved 80 mm or turned 4° — the *keyframe*
   rule. Without it a parked robot burns the same rotation in ten times a second and over-sharpens
   the map into something nothing matches.

It is brute force on purpose (rule 10). There is no gradient descent to diverge and no line search
to tune, and when it misbehaves you can print the score surface and look at it.

### The gates, which are the part that matters

Slam's native failure is **silent drift**, so the gates carry the design exactly as `pose.py`'s do.
A missing pose costs nothing; a confident wrong one corrupts the map for the rest of the run.

| Gate | Rejects |
| --- | --- |
| `MIN_SCORE` | a scan that is not anywhere the map has been |
| edge of window | a winner on the boundary — the real optimum is outside, so this is the best of a bad set |
| `_peaked` | a **plateau**. Slide a scan along a featureless corridor and the score does not change, because position along that corridor is genuinely not observable from a lidar. No algorithm recovers it and a number there would be invention |

`_peaked` is **axis-aligned**, so it catches a corridor lying along x or y and is weaker for one
lying diagonally. Tightening it needs the eigenvectors of the score surface, not a bigger constant.

After `RELOCK_AFTER` failures in a row it throws the map away and starts a new frame, signalling
`take_relock()` so the server clears the grid — same contract `pose.py` has, for the same reason:
the new frame has no relation to the old one, so every point on the map is now in the wrong place.

---

## 4. The one bug worth knowing about

The first run tracked **28% of scans** and ended 4 metres out. The cause is not obvious and will
cost you hours if you meet it again in another form:

> **Ties are the common case, not the exception.** Shift a scan by less than a cell and nothing in
> the field changes, so a whole plateau of candidate poses scores *identically*. `np.argmax` then
> returns the lowest index — which is the **corner of the search window**, the largest movement on
> offer. Every scan, the matcher was handed the most extreme pose that tied for best.

The fix is a tie-break towards the prior: among equal scores, prefer the one closest to where Scout
already was. With no odometry, *"Scout barely moved"* is the only prior available and at 10 Hz it is
a good one. `TIE_MM` and `TIE_DEG` are sized to separate equals and never to outrank a real
difference in score.

**28% → 100%, and the median error went from 2631 mm to 18 mm, on that change alone.**

Two smaller fixes rode along: the coarse field went from 200 mm cells to 100 mm, and the fine window
was widened to cover a whole coarse step either way — otherwise a coarse answer that is right to
within its own quantisation still lands outside the fine search and gets thrown away as an edge hit.

---

## 5. What changed in the code

Three commits on `main`, pushed: `6689b49`, `a4809ed`, `c5276c7`.

- **`pi/scout/slam.py` — new.** Everything in §3. Read its module docstring first; the failure modes
  are written down there.
- **`pi/tools/check_slam.py` — new.** Replays the recorded run and scores against its ground truth.
- **`pi/scout/mapping.py`** — the grid can be sized without a room. Two things quietly leaned on the
  room rectangle and both needed another rule:
  - **the canvas.** No room means no size, so slam lays out a fixed 12 m canvas with the start point
    at its centre, and `frame()` **crops to the part actually seen** — a full canvas is 57 kB of
    cells a second on a phone hotspot, one room's worth is a fifth of that.
  - **wall vs object.** `_is_wall` was "near the room's edge". Without a room, *every wall becomes an
    obstacle cluster* and Scout stops to photograph and name each one. The replacement: a cluster
    that runs longer than any furniture (`MAX_OBJECT_MM`) is the building.
- **`pi/scout/server.py`** — `to_room` can no longer call a point a wall by its distance to one, so
  it asks instead whether the point sits on something already reported as an obstacle. Only the
  *wording* of a width verdict rides on this ("between two walls" vs "between a wall and a chair").
- **`pi/scout/main.py`, `config.py`** — `SCOUT_POSE` picks the engine, `SCOUT_MAP_SPAN_MM` sizes the
  canvas. numpy is a hard dependency of `slam.py` alone; missing, the service logs it loudly and
  falls back to `rect` rather than failing to start (rule 9).

**The `rect` path is byte-identical.** Map frames, clusters and `claim_ahead` all diff zero against
the recorded run, and `check_room.py` (8/8) and `check_audit.py` (17/17) pass unchanged.

---

## 6. ⚠️ What has NOT been done

**None of this has met a real lidar.** Every number above is the simulated recorded run. That is the
single biggest gap and it is also the cheapest thing on the list.

---

## 7. What to do next, in order

1. **Restart the service.** Anything already running predates this and is still on the rectangle
   fitter. Watch for the startup line: `POSE: slam, scan matching against the map so far`.
2. **Point it at the real lidar and walk it through a doorway into a second room** — something the
   rectangle fitter physically cannot do. ~10 minutes, no motors needed: position comes from the
   scans, so carrying the lidar is equivalent to driving it. Walk slowly and keep it level.
   **This is the demo beat.** It is also the first honest test this code has had.
3. **Record that lap** (`pi/tools/record.py`), run `check_slam.py` against it, and **put the drift
   number in the pitch.**
4. **Tape-measure the room and compare it to the map.** With no fitted rectangle there is no
   `room frame locked: W x L mm` line to sanity-check against any more, so the tape is now the
   *only* check that the map is the right size. `HANDOFF-LIDAR.md` §3 shows a U-shaped space locking
   at two to three times its true size while looking perfectly healthy — that failure mode belonged
   to the old fitter, but "believe the tape" survives it.
5. **Watch the terminal for the rejection reasons.** `slam: no match for N scans (<why>)` names
   which gate fired. If it is `the score surface is flat`, that is the corridor case and it is
   working correctly. If it is `score X under 0.30` constantly, the map and the scans have come
   apart — that is the interesting bug.
6. **Check the tick cost on the Pi**, not on the Mac. 5.2 ms here, budget 100 ms, a Pi 4 is roughly
   3× slower. Expected fine; confirm rather than assume. If it is tight, cut `MAX_POINTS` to 120
   before touching the search window.

### If slam misbehaves on the day

`SCOUT_POSE=rect`, build the room oblong and closed per `RUNBOOK.md` §0, and you are back to the
validated 2.0 mm path with nothing lost. **Clearance still needs no position at all** and fires
width verdicts either way, so the demo degrades honestly even if both fitters fail.

---

## 8. Tools

```
cd pi && .venv/bin/python tools/check_slam.py     # replay the recorded run through slam, scored against truth
cd pi && .venv/bin/python tools/check_room.py     # the rect fitter in rooms of any shape — no hardware
cd pi && .venv/bin/python tools/check_audit.py    # 17 clearance and gap checks — no hardware
cd pi && .venv/bin/python tools/check_pose.py     # LIVE: walks every rect-fit gate, saves tools/scan-dump.json
cd pi && SCOUT_POSE=rect .venv/bin/python -m scout    # the old engine
```

`check_slam.py` aligns the frames on the **first** scan only, never a best fit over the run. A best
fit would spread the drift evenly and flatter the result; aligning on the start means the error at
the end is the error a demo would actually show. It takes a path, so it runs against any recorded
run, not just the bundled one.

**Any change to `slam.py` must be re-scored with `check_slam.py`**, the same standard `pose.py` is
held to. Tracking percentage and drift both matter and they fail independently.

---

## 9. Do not redo these

- **Do not remove the tie-break towards the prior.** §4. It looks like a fudge factor and it is
  worth 28% → 100% of scans.
- **Do not delete `pose.py`.** It is strictly better than slam inside a closed rectangle — 2.0 mm
  against 18 mm, and structurally incapable of drifting. It is the fallback and it is validated.
- **Do not add loop closure now.** It is the right answer to drift and it is a pose-graph optimizer.
  Not at hour 22.
- **Do not widen the fine search window to paper over edge rejections.** An edge hit means the
  coarse stage handed over a bad answer; fix the stage that was wrong.
- **Do not make `_peaked` stricter with a bigger constant.** A diagonal corridor needs the
  eigenvectors of the score surface, not a larger threshold — a larger one just starts rejecting
  good poses in ordinary rooms.
- **Do not go looking for bugs in the express-scan decode.** Audited against the SDK line by line
  last session (`HANDOFF-LIDAR.md` §1).

---

## 10. Judgement calls, so they are not silently reversed

- **Slam is the default, `rect` is one variable away.** Neither was deleted.
- **`room` is `null` under slam.** The wire contract already allowed it (`protocol.ts:15`) and
  `MapView.tsx` already had a room-less rendering path someone deliberately wrote, so nothing broke
  — but what `null` *usually means* has changed, and the other code owners should know (rule 2).
- **Every gate fails closed**, exactly as `pose.py`'s do. A missing pose costs nothing; a confident
  wrong one corrupts the map for the rest of the run.
- **A plateau is refused rather than guessed at.** Position along a featureless corridor is not
  observable from a lidar. Reporting a number there would be inventing one.
- **The map frame is cropped to what has been seen**, so the canvas can be generous without the
  hotspot paying for it.
- **numpy missing disables slam only**, and says so loudly, rather than taking the service down.
- **Ramps are still labelled and never judged.** Nothing here touches that. Scout still measures no
  slope, and clearance width is still the only building-code verdict.
