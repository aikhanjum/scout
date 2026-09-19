#!/usr/bin/env python3
"""Why is there no pose? Reads one real scan from a running Scout and reports every check.

    cd pi && .venv/bin/python tools/check_pose.py            # localhost
    cd pi && .venv/bin/python tools/check_pose.py scout.local

The lidar port is held exclusively by the service, so this goes through the WebSocket rather than
opening the device. It saves the scan next to itself so the numbers can be looked at later, or sent
to someone who is not standing in the room.
"""
import asyncio
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scout import pose as P  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
if ":" not in HOST:
    HOST += ":8080"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan-dump.json")


def report(scan):
    print(f"\n--- scan ---")
    if not scan:
        print("FAIL  the frame carries no scan at all: the service says the lidar is not connected")
        return
    print(f"      {len(scan)} entries (want 360)")
    if len(scan) != 360:
        print("FAIL  wrong length, so pose never even tries")
        return

    hits = [mm for mm in scan if mm > 0]
    print(f"      {len(hits)} returns, {360 - len(hits)} no-returns ({100 * (360 - len(hits)) / 360:.0f}% empty)")
    if hits:
        hits_sorted = sorted(hits)
        print(f"      range: min {hits_sorted[0]} mm, median {hits_sorted[len(hits_sorted) // 2]} mm, max {hits_sorted[-1]} mm")

    pts = P.scan_points(scan)
    print(f"\n1. enough returns?      {len(pts)} >= {P.MIN_POINTS}   {'ok' if len(pts) >= P.MIN_POINTS else 'FAIL'}")
    if len(pts) < P.MIN_POINTS:
        print("   Too little came back. The lidar is spinning but barely seeing anything: check it is")
        print("   not boxed in, not facing a mirror, and that nothing is sitting on top of it.")
        return

    hull = P.convex_hull(pts)
    print(f"2. hull built?          {len(hull)} corners >= 3   {'ok' if len(hull) >= 3 else 'FAIL'}")
    if len(hull) < 3:
        return

    cx, cy, theta, a, b = P.min_area_rect(hull)
    w, l = 2 * a, 2 * b
    lo, hi = P.MIN_SIDE_MM, P.MAX_SIDE_MM
    size_ok = lo <= w <= hi and lo <= l <= hi
    print(f"3. room-sized?          {w:.0f} x {l:.0f} mm, both within {lo}..{hi}   {'ok' if size_ok else 'FAIL'}")
    if not size_ok:
        print("   The fitted rectangle is not a plausible room. Too big usually means the lidar is")
        print("   seeing through a doorway into the next space: close the door. Too small means it is")
        print("   boxed in by something close on all sides.")

    frac = P._on_edge_fraction(pts, cx, cy, theta, a, b)
    edge_ok = frac >= P.MIN_ON_EDGE
    print(f"4. is it a rectangle?   {frac:.0%} of returns lie on it, need {P.MIN_ON_EDGE:.0%}   {'ok' if edge_ok else 'FAIL'}")
    if not edge_ok:
        print("   This is the usual one. The room is not reading as four straight walls, because of")
        print("   furniture along them, people standing in it, an alcove or a bay, or a lidar that is")
        print("   not level. Every degree of tilt bends the walls outward.")
        print(f"   If the room really is this cluttered, MIN_ON_EDGE ({P.MIN_ON_EDGE}) in pi/scout/pose.py")
        print("   is the number to relax -- but clear the floor and re-run this first.")

    p = P.Pose()
    ok = p.update(scan, moving=True)
    print(f"\nverdict: {'POSE OK ' + str(p.snapshot()) if ok else 'NO POSE'}")
    if ok:
        print("   It locks on this scan. If the dashboard still says NO POSE, the fit is flickering:")
        print("   run this a few times and see how often it passes.")


async def main():
    import aiohttp
    url = f"http://{HOST}/ws"
    print(f"connecting to {url} ...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(url, timeout=10) as ws:
                for _ in range(40):
                    msg = await asyncio.wait_for(ws.receive(), timeout=10)
                    if msg.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    f = json.loads(msg.data)
                    if f.get("type") != "telem":
                        continue
                    print(f"lidar={f.get('lidar')}  pose={f.get('pose')}  room={f.get('room')}  "
                          f"clearance={f.get('clearance_mm')} mm  gaps={len(f.get('gaps') or [])}")
                    with open(OUT, "w") as fh:
                        json.dump({"scan": f.get("scan"), "gaps": f.get("gaps")}, fh)
                    report(f.get("scan"))
                    print(f"\nscan saved to {OUT}")
                    return
                print("no telemetry arrived")
    except Exception as e:
        print(f"could not reach {url}: {type(e).__name__}: {e}")
        print("Is `python -m scout` running, and is the port right?")


asyncio.run(main())
