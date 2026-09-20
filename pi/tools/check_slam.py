#!/usr/bin/env python3
"""Does the scan matcher track, and how far does it drift? Run it before the robot exists.

    cd pi && .venv/bin/python tools/check_slam.py

`data/runs/room-scan.ndjson` carries 660 scans at 10 Hz with the true pose in *every* frame, so it
is free ground truth: replay the scans through `slam.Slam`, ignore the truth except to score
against it, and see what comes out.

The frames are aligned on the **first** scan only. Slam starts wherever it starts and calls that
the origin, so one rigid transform takes its frame to the run's. Deliberately not a best fit over
the whole run: a best fit would spread the drift evenly and flatter the result. Aligning on the
start means the error you see at the end is the error a demo would show after driving that far,
which is the number worth quoting.

Position error and heading error are reported apart, because a frame flip and a slow drift are
different bugs. The last line is the one for the pitch.
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scout import slam as S  # noqa: E402

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "data", "runs", "room-scan.ndjson")


def pct(v, p):
    if not v:
        return float("nan")
    return sorted(v)[min(len(v) - 1, int(len(v) * p))]


def main(path=RUN):
    frames = [json.loads(l) for l in open(path) if '"telem"' in l]
    frames = [f for f in frames if f.get("scan") and f.get("pose")]
    if not frames:
        print("no usable frames in", path)
        return 1
    print("%d scans from %s" % (len(frames), os.path.basename(path)))

    sl = S.Slam()
    # the transform from slam's frame to the run's, taken from the first frame alone
    f0 = frames[0]
    h0 = math.radians(f0["heading_deg"])
    c0, s0 = math.cos(h0), math.sin(h0)

    pos, head, scores, lost, relocks = [], [], [], 0, 0
    t_start = time.monotonic()
    for f in frames:
        if not sl.update(f["scan"], moving=True):
            lost += 1
            continue
        if sl.take_relock():
            relocks += 1
        x = f0["x_mm"] + sl.x * c0 - sl.y * s0
        y = f0["y_mm"] + sl.x * s0 + sl.y * c0
        th = sl.heading + f0["heading_deg"]
        pos.append(math.hypot(x - f["x_mm"], y - f["y_mm"]))
        head.append(abs((th - f["heading_deg"] + 180) % 360 - 180))
        scores.append(sl.score)
    ms = (time.monotonic() - t_start) / len(frames) * 1000

    n = len(pos)
    print()
    print("pose on %d/%d scans (%.0f%%), %d refused, %d frame restarts"
          % (n, len(frames), 100.0 * n / len(frames), lost, relocks))
    if not n:
        print("FAIL: never tracked")
        return 1
    print("position error   median %6.0f mm   p95 %6.0f mm   max %6.0f mm"
          % (pct(pos, 0.5), pct(pos, 0.95), max(pos)))
    print("heading error    median %6.1f deg  p95 %6.1f deg  max %6.1f deg"
          % (pct(head, 0.5), pct(head, 0.95), max(head)))
    print("match score      median %6.2f      min %6.2f" % (pct(scores, 0.5), min(scores)))
    print("%.1f ms per scan (budget is 100 ms at 10 Hz; a Pi 4 is some 3x slower than this Mac)"
          % ms)
    print()
    print("DRIFT AFTER THE FULL RUN: %.0f mm, %.1f deg  <- the number for the pitch"
          % (pos[-1], head[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
