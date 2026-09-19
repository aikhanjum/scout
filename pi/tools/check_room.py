#!/usr/bin/env python3
"""Does pose survive the room we are actually going to build? Run it before the robot exists.

    cd pi && .venv/bin/python tools/check_room.py

`data/runs/room-scan.ndjson` is one 4210 x 5090 room, so nothing in the repo exercises a *square*
one -- and a square room is where the fit gets dangerous. Telling the four 90-degree readings of a
rectangle apart normally leans on its shape, and when both sides are equal that cue is gone. The
failure is silent: the room frame turns a quarter turn, the pose still looks confident, and every
obstacle already on the map is now in the wrong place.

So this builds rooms of several shapes, drives Scout round each one, casts a real 360-ray scan at
every step and checks the pose against the truth it came from. A flip shows up as a heading error
near 90 degrees, which is reported separately from position error because it is a different bug.

Rooms here are perfect rectangles with sharp corners, which is the best case by construction. A room
that passes here can still fail in the lab; a room that fails here will certainly fail in the lab.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scout import pose as P  # noqa: E402

HZ = 10.0                # A2M8 rotations per second (docs/SPEC.md), so one pose fix per scan
CRUISE_MMS = 400.0       # driving speed along a wall
TURN_DEGS = 70.0         # in-place turn rate at a corner
STANDOFF_MM = 300.0      # how far off the wall Scout follows
JITTER_MM = 12.0         # range noise, same order as gen.js uses


def _rng(seed=12345):
    """A deterministic little LCG, so a failure here is always reproducible."""
    s = seed

    def nxt():
        nonlocal s
        s = (1103515245 * s + 12345) % (1 << 31)
        return s / (1 << 31) * 2.0 - 1.0
    return nxt


def segments(w, l, obstacles=()):
    segs = []

    def box(x0, y0, x1, y1):
        segs.extend([(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)])
    box(0, 0, w, l)
    for o in obstacles:
        box(*o)
    return segs


def cast(px, py, heading_deg, segs, jit):
    """One 360-entry scan from a pose, in the robot frame (PROTOCOL.md section 6)."""
    scan = [0] * 360
    for i in range(360):
        a = math.radians(heading_deg + i)
        dx, dy = math.cos(a), math.sin(a)
        best = float("inf")
        for ax, ay, bx, by in segs:
            sx, sy = bx - ax, by - ay
            den = dx * sy - dy * sx
            if abs(den) < 1e-9:
                continue
            t = ((ax - px) * sy - (ay - py) * sx) / den
            u = ((ax - px) * dy - (ay - py) * dx) / den
            if t >= 0 and 0 <= u <= 1 and t < best:
                best = t
        scan[i] = 0 if best == float("inf") else max(0, int(round(best + jit() * JITTER_MM)))
    return scan


def lap(w, l, laps=1.0):
    """Wall-follow the perimeter: drive a side, turn 90 degrees in place, repeat."""
    x0, y0 = STANDOFF_MM, STANDOFF_MM
    x1, y1 = w - STANDOFF_MM, l - STANDOFF_MM
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    x, y = corners[0]
    heading = 0.0
    out = []
    for leg in range(int(4 * laps)):
        tx, ty = corners[(leg + 1) % 4]
        want = math.degrees(math.atan2(ty - y, tx - x))
        while abs((want - heading + 180) % 360 - 180) > 1.0:      # turn in place
            d = (want - heading + 180) % 360 - 180
            heading += max(-TURN_DEGS / HZ, min(TURN_DEGS / HZ, d))
            out.append((x, y, (heading + 180) % 360 - 180, True))
        dist = math.hypot(tx - x, ty - y)
        steps = max(1, int(dist / (CRUISE_MMS / HZ)))
        for s in range(steps):                                     # drive the side
            f = (s + 1) / steps
            out.append((x + (tx - x) * f, y + (ty - y) * f, (heading + 180) % 360 - 180, True))
        x, y = tx, ty
    return out


def run(name, w, l, obstacles=(), laps=1.0):
    segs = segments(w, l, obstacles)
    jit = _rng()
    path = lap(w, l, laps)
    p = P.Pose()
    ref = None
    perr, herr, relocks, flips = [], [], 0, 0

    for tx, ty, th, moving in path:
        scan = cast(tx, ty, th, segs, jit)
        if not p.update(scan, moving=moving):
            continue
        if p.take_relock():
            relocks += 1
            ref = None
        if ref is None:
            # Scout's room frame is its own; align it to the truth on the first fix, then every
            # later disagreement is real error rather than a difference of convention.
            d = math.radians(th - p.heading)
            c, s = math.cos(d), math.sin(d)
            ref = (c, s, tx - (p.x * c - p.y * s), ty - (p.x * s + p.y * c), th - p.heading)
            continue
        c, s, ox, oy, dth = ref
        perr.append(math.hypot(ox + (p.x * c - p.y * s) - tx, oy + (p.x * s + p.y * c) - ty))
        e = abs((p.heading + dth - th + 180) % 360 - 180)
        herr.append(e)
        if e > 45.0:
            flips += 1

    n = len(path)
    if not perr:
        print(f"  {name:22s} NO POSE AT ALL")
        return False
    perr_s, herr_s = sorted(perr), sorted(herr)
    q = lambda A, f: A[min(len(A) - 1, int(len(A) * f))]
    ok = flips == 0 and perr_s[-1] < 150 and len(perr) / n > 0.6
    print(f"  {name:22s} room {str(p.room):>14s}  poses {100 * len(perr) / n:3.0f}%  "
          f"pos median {q(perr_s, .5):5.1f} max {perr_s[-1]:6.1f} mm  "
          f"heading max {herr_s[-1]:4.1f} deg  relocks {relocks}  flips {flips}   "
          f"{'ok' if ok else 'FAIL'}")
    return ok


# Obstacles are boxes in the room frame. Keep them off the wall-following lane, which runs
# STANDOFF_MM in from each wall -- a box straddling it puts Scout inside the obstacle, and the
# scan that comes back is nonsense through no fault of pose.py.
CASES = [
    # name, width, length, obstacles
    ("oblong 4210x5090", 4210, 5090, [(1700, 900, 2260, 1650), (3320, 3200, 3700, 3700)]),
    ("square 2200", 2200, 2200, []),
    ("square 2200 + obstacles", 2200, 2200, [(900, 900, 1300, 1300), (2000, 800, 2200, 1200)]),
    ("near-square 2200x2260", 2200, 2260, []),
    ("square 3000", 3000, 3000, []),
    ("small square 1600", 1600, 1600, []),
    ("square 2200, two laps", 2200, 2200, [(900, 900, 1300, 1300)], 2.0),
]

def blind_turn(w, l, turn_deg=90.0, blind=12, at=(700.0, 800.0)):
    """Blind Scout, turn it on the spot, let it see again. How far out does it come back?

    This is the scenario that decides whether the room may be square. Someone leans over the lidar
    just as Scout turns a corner: the fit is lost for a second, the heading changes by about a
    quarter turn, and the next scan has to be matched back to the room frame with nothing but that
    scan. In an oblong room the shape settles it. In a square one the turned scan and the unturned
    scan are the same picture, and the wrong answer is the one that looks more continuous.
    """
    segs = segments(w, l)
    jit = _rng()
    p = P.Pose()
    ref = None
    worst = 0.0
    x, y, th = at[0], at[1], 0.0
    for n in range(140):
        dark = 20 <= n < 20 + blind
        if dark:
            th += turn_deg / blind                       # turning on the spot, seeing nothing
        scan = [0] * 360 if dark else cast(x, y, (th + 180) % 360 - 180, segs, jit)
        if not p.update(scan, moving=True):
            continue
        if p.take_relock():
            ref = None
        if ref is None:
            d = math.radians(th - p.heading)
            c, s = math.cos(d), math.sin(d)
            ref = (c, s, 0, 0, th - p.heading)
            continue
        if n > 20 + blind:
            worst = max(worst, abs((p.heading + ref[4] - th + 180) % 360 - 180))
    return worst


if __name__ == "__main__":
    print(f"\nwall-following one lap, {HZ} scans/s, {JITTER_MM:.0f} mm range noise\n")
    good = [run(*c) for c in CASES]

    print("\nturning ~90 degrees while blinded, then matching back to the room frame:")
    sq = blind_turn(2200, 2200)
    ob = blind_turn(4210, 5090)
    print(f"  square 2200x2200       comes back {sq:5.1f} deg out   "
          f"{'-- KNOWN, unfixable: build the room oblong' if sq > 45 else 'ok'}")
    print(f"  oblong 4210x5090       comes back {ob:5.1f} deg out   "
          f"{'ok' if ob <= 5 else 'FAIL -- shape should have settled this'}")
    good.append(ob <= 5)

    print(f"\n{sum(good)}/{len(good)} checks pass "
          f"(pass = no frame flip, max position error under 150 mm, pose on most scans)\n")
    sys.exit(0 if all(good) else 1)
