#!/usr/bin/env python3
"""Checks for scout/audit.py. Plain asserts, no test framework (see CLAUDE.md rule 6).

    cd pi && .venv/bin/python tools/check_audit.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scout.audit import Audit  # noqa: E402

CFG = {"slope_limit_deg": 4.76, "width_limit_mm": 860, "scale": 0.25, "width_offset_mm": 0}
LIMIT = 860 * 0.25          # 215 mm on the 1:4 course
checks = 0


def gap(width, evidence="see_through", a0=-12.0, a1=12.0, mm=600):
    return {"a0": a0, "mm0": mm, "a1": a1, "mm1": mm, "width_mm": width,
            "span_deg": a1 - a0, "evidence": evidence}


def run(steps):
    """steps: [(seconds, width_mm, gaps)]. Returns every event emitted."""
    a = Audit(dict(CFG))
    out, t = [], 0.0
    for secs, width_mm, gaps in steps:
        n = max(1, int(secs * 10))
        for _ in range(n):
            t += 0.1
            events, _stop = a.step(t, 0.0, width_mm, gaps)
            out.extend(events)
    return out


def check(name, got, want):
    global checks
    checks += 1
    if got != want:
        print(f"FAIL  {name}\n      got  {got}\n      want {want}")
        sys.exit(1)
    print(f"ok    {name}")


def kinds(events):
    return [e["kind"] for e in events]


def values(events):
    return [e["value"] for e in events]


# ---- unverified and step gaps must never reach the audit ------------------
check("an unverified gap never fires, however long it is held",
      kinds(run([(6.0, 0, [gap(190, "unverified")]), (2.0, 0, None)])), [])
check("a step gap never fires",
      kinds(run([(6.0, 0, [gap(190, "step")]), (2.0, 0, None)])), [])

# ---- a see_through gap does -----------------------------------------------
evs = run([(1.0, 0, None), (2.0, 0, [gap(190)]), (2.0, 0, None)])
check("a narrow see_through gap ahead fires one width_fail", kinds(evs), ["width_fail"])
check("  carrying the gap's width", values(evs), [190])

evs = run([(1.0, 0, None), (2.0, 0, [gap(250)]), (2.0, 0, None)])
check("a see_through gap wider than the limit fires width_pass", kinds(evs), ["width_pass"])

# ---- scoping ---------------------------------------------------------------
check("a gap off to the side does not fire",
      kinds(run([(2.0, 0, [gap(190, a0=100.0, a1=130.0)]), (2.0, 0, None)])), [])
check("a gap too far away does not fire",
      kinds(run([(2.0, 0, [gap(190, mm=4000)]), (2.0, 0, None)])), [])
check("a gap wider than the interesting band does not open a pinch",
      kinds(run([(2.0, 0, [gap(900)]), (2.0, 0, None)])), [])

# ---- one doorway, one event ------------------------------------------------
# Seen ahead for 2 s, driven through for 1 s, then clear. The drive-through is the
# better measurement, so it must be the value, and there must be exactly one event.
evs = run([(1.0, 0, None), (2.0, 0, [gap(190)]), (1.0, 189, [gap(190)]), (2.0, 0, None)])
check("seeing a gate then driving through it gives one event", kinds(evs), ["width_fail"])
check("  carrying the smaller drive-through measurement", values(evs), [189])

# ---- the existing width_mm pinch is unchanged ------------------------------
evs = run([(1.0, 0, None), (1.0, 189, None), (2.0, 0, None)])
check("width_mm alone still fires one width_fail", kinds(evs), ["width_fail"])
check("  with the minimum seen", values(evs), [189])
evs = run([(1.0, 0, None), (1.0, 250, None), (2.0, 0, None)])
check("width_mm alone still fires width_pass above the limit", kinds(evs), ["width_pass"])
check("a lidar-less run (gaps None) behaves exactly as before",
      kinds(run([(1.0, 0, None), (1.0, 189, None), (2.0, 0, None)])), ["width_fail"])

# ---- flicker must not refire -----------------------------------------------
# The scan arrives at 2 Hz and a gap's evidence can change between rotations. One
# missed scan is flicker, not a second doorway.
flicker = [(1.0, 0, None)]
for _ in range(4):
    flicker += [(0.5, 0, [gap(190)]), (0.5, 0, None)]
flicker += [(2.0, 0, None)]
check("a gap flickering off for one scan gives one event", kinds(run(flicker)), ["width_fail"])

check("a gap that really goes away, then a second gap, gives two events",
      kinds(run([(1.0, 0, None), (1.0, 0, [gap(190)]), (2.5, 0, None),
                 (1.0, 0, [gap(250)]), (2.0, 0, None)])),
      ["width_fail", "width_pass"])

# ---- two doorways stay two events -----------------------------------------
evs = run([(1.0, 0, None), (1.0, 189, None), (2.0, 0, None), (1.0, 250, None), (2.0, 0, None)])
check("two gates give two events", kinds(evs), ["width_fail", "width_pass"])

print(f"\n{checks} checks passed")
