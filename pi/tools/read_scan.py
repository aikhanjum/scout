#!/usr/bin/env python3
"""Read the live lidar for someone who cannot see the dashboard. Samples telemetry for a few
seconds and says, in words, what clearance() sees and why, using the audit's own rules.

    .venv/bin/python tools/read_scan.py [--url ws://localhost:8080/ws] [--seconds 5]
"""
import argparse
import asyncio
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402

from scout import audit  # noqa: E402

SECTOR = getattr(audit, "SECTOR_DEG", 45)
SLICE = getattr(audit, "SLICE_MM", 300)
MAX_SIDE = getattr(audit, "MAX_SIDE_MM", 2500)
MAX_DROP = getattr(audit, "MAX_DROPOUT", 0.6)
FRONT_DEG = getattr(audit, "FRONT_DEG", 15)
FRONT_CLEAR = getattr(audit, "FRONT_CLEAR_MM", 250)
DROP_DEG = getattr(audit, "DROPOUT_DEG", SECTOR)


def nearest(scan, centre, half):
    best = None
    for d in range(centre - half, centre + half + 1):
        mm = scan[d % 360]
        if mm > 0 and (best is None or mm < best[0]):
            best = (mm, d % 360)
    return best


def empty_share(scan, centre, half):
    bins = [scan[d % 360] for d in range(centre - half, centre + half + 1)]
    return sum(1 for b in bins if b <= 0) / len(bins)


def side_bound(scan, centre):
    """The return clearance() would pick on this side, or why it has none."""
    drop = empty_share(scan, centre, DROP_DEG)
    if drop > MAX_DROP:
        return None, f"{drop:.0%} of the {2 * DROP_DEG + 1} beams there came back empty (limit {MAX_DROP:.0%})"
    best = None
    for d in range(centre - SECTOR, centre + SECTOR + 1):
        mm = scan[d % 360]
        if mm <= 0:
            continue
        a = math.radians(d % 360)
        along, off = mm * math.cos(a), abs(mm * math.sin(a))
        if abs(along) > SLICE or off > MAX_SIDE:
            continue
        if best is None or off < best[0]:
            best = (off, d % 360, mm)
    if best is None:
        return None, f"no return within {SLICE} mm of straight abeam and closer than {MAX_SIDE / 1000:.1f} m"
    return best, f"{best[0]:.0f} mm out at bearing {best[1]} (range {best[2]} mm)"


def bearing_words(b):
    b = b % 360
    if b <= 180:
        return f"{b} deg left" if b else "dead ahead"
    return f"{360 - b} deg right"


async def sample(url, seconds):
    frames = []
    async with aiohttp.ClientSession() as s, s.ws_connect(url) as ws:
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=max(0.05, end - loop.time()))
            except asyncio.TimeoutError:
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            f = json.loads(msg.data)
            if f.get("type") == "telem":
                frames.append(f)
    return frames


def report(frames, seconds):
    if not frames:
        print("No telemetry arrived. Is the service running?")
        return
    live = [f for f in frames if f.get("lidar") and len(f.get("scan") or []) == 360]
    print(f"{len(frames)} frames in {seconds} s; {len(live)} with a live 360-bin scan.")
    if not live:
        print("The lidar is not delivering scans (lidar=false). Nothing else to read.")
        return
    f = live[-1]
    scan = f["scan"]
    vals = [x["clearance_mm"] for x in live]
    nums = [v for v in vals if v > 0]

    # clearance
    if nums and len(nums) == len(vals):
        print(f"CLEARANCE: a number. {statistics.median(nums):.0f} mm median over {len(nums)} frames "
              f"(min {min(nums)}, max {max(nums)}), {'steady' if max(nums) - min(nums) <= 40 else 'jumpy'}.")
    elif nums:
        print(f"CLEARANCE: flickering. {len(nums)} of {len(vals)} frames gave a number (median {statistics.median(nums):.0f} mm), the rest refused.")
    else:
        print("CLEARANCE: refusing (0) in every frame.")
    # why, on the last frame
    ahead_block = None
    for d in range(-FRONT_DEG, FRONT_DEG + 1):
        mm = scan[d % 360]
        if 0 < mm < FRONT_CLEAR:
            ahead_block = (mm, d % 360)
            break
    if ahead_block:
        print(f"  reason: something {ahead_block[0]} mm away at {bearing_words(ahead_block[1])} is inside the {FRONT_CLEAR} mm front zone, so it counts as blocked, not narrow.")
    lb, lwhy = side_bound(scan, 90)
    rb, rwhy = side_bound(scan, 270)
    print(f"  left bound:  {'OK, ' if lb else 'NONE: '}{lwhy}")
    print(f"  right bound: {'OK, ' if rb else 'NONE: '}{rwhy}")
    if lb and rb:
        print(f"  -> clearance from this frame = {lb[0]:.0f} + {rb[0]:.0f} = {lb[0] + rb[0]:.0f} mm")

    # nearest things
    for name, c in (("ahead", 0), ("left", 90), ("behind", 180), ("right", 270)):
        n = nearest(scan, c, 10)
        print(f"  nearest {name:6s} (within 10 deg): {'nothing returned' if n is None else f'{n[0]} mm at {bearing_words(n[1])}'}")
    print(f"  empty beams: left side {empty_share(scan, 90, DROP_DEG):.0%}, right side {empty_share(scan, 270, DROP_DEG):.0%}, "
          f"ahead {empty_share(scan, 0, 20):.0%}, behind {empty_share(scan, 180, 20):.0%}, whole ring {empty_share(scan, 0, 180):.0%}")

    # gaps
    gaps = f.get("gaps") or []
    seen = [g for g in gaps if g.get("evidence") == "see_through"]
    others = len(gaps) - len(seen)
    doorish = sorted((g for g in seen if 500 <= g["width_mm"] <= 1600), key=lambda g: abs(g["mid_deg"]))
    print(f"  gaps: {len(seen)} open (see-through), {others} unverified/step.")
    if doorish:
        g = doorish[0]
        print(f"  doorway candidate: {g['width_mm']} mm wide, centred {bearing_words(round(g['mid_deg']))}, "
              f"edges at {g['mm0']} mm (bearing {g['a0']:.0f}) and {g['mm1']} mm (bearing {g['a1']:.0f}).")
        for g in doorish[1:3]:
            print(f"  also: {g['width_mm']} mm at {bearing_words(round(g['mid_deg']))}")
    elif seen:
        widest = max(seen, key=lambda g: g["width_mm"])
        print(f"  no door-sized opening; widest open gap is {widest['width_mm']} mm at {bearing_words(round(widest['mid_deg']))}.")
    print(f"  pose: {'locked' if f.get('pose') else 'none'}{'  room ' + json.dumps(f['room']) if f.get('pose') and f.get('room') else ''}; mode {f.get('mode')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8080/ws")
    ap.add_argument("--seconds", type=float, default=5)
    a = ap.parse_args()
    report(asyncio.run(sample(a.url, a.seconds)), a.seconds)


if __name__ == "__main__":
    main()
