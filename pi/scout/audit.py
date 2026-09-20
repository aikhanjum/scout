"""Clearance: how wide the way through is, and whether a wheelchair fits (PROTOCOL.md section 6).

Width is the one thing Scout judges against a building-code limit, so it is measured directly
rather than inferred. Take a thin slice straight across Scout's path, through where it is standing.
Of the returns falling in that slice, the closest on the left and the closest on the right are the
two things a wheelchair would have to pass between, and their separation is the clearance.

Neither side has to be a wall, so this catches a bin beside a doorway as readily as a doorway --
which is why v1's fixed pair of beams at plus and minus ninety degrees was not enough. Against the
simulated course it reads a 510 mm slot as 504 mm.

Scout reports no clearance at all in an open room. That is deliberate: two walls four metres apart
are not a gap anyone has to fit through, and calling them one would bury the real pinch points.

It also reports nothing when a side of the slice is mostly no-returns. A lidar gets nothing back
from glass, gloss black and some dark carpet, exactly as if the space were empty, so a near surface
that does not reflect is silently skipped and the next surface behind it is measured instead. The
gap then comes out **too wide**, which is the one error direction that matters here: it turns a
doorway a wheelchair cannot use into a pass. Refusing to measure a sparse sector is the safe
failure. This is the same distinction `gaps.py` draws as `unverified`, applied to the abeam slice.
"""
import math

# A gap from the scan frame only counts towards the width audit when Scout could
# plausibly drive through it: roughly ahead, and close by. Without this a narrow
# gap across the room or behind Scout would fire an event nobody asked about.
GAP_AHEAD_DEG = 60.0
GAP_MAX_RANGE_MM = 2500.0
PINCH_MAX_S = 8.0          # hard cap: an approach plus the drive through is longer than 3 s
GAP_LATCH_S = 1.0          # a gap stays "seen" this long after the last scan that had it


def gap_bearing(g):
    """Middle bearing of a gap, in (-180, 180]. a0 to a1 runs counter-clockwise."""
    span = (g["a1"] - g["a0"]) % 360.0
    mid = (g["a0"] + span / 2.0) % 360.0
    return mid - 360.0 if mid > 180.0 else mid


def audit_width_from_gaps(gaps, threshold_mm):
    """The narrowest gap that may open a width pinch, or 0 when there is none.

    Only `see_through` gaps count. An `unverified` gap is an arc where nothing came
    back, where an opening and a surface that does not reflect are indistinguishable
    in one rotation, so it can never become a measurement. A `step` gap is the corner
    of an object, not an opening."""
    best = 0
    for g in gaps or ():
        if g.get("evidence") != "see_through":
            continue
        if abs(gap_bearing(g)) > GAP_AHEAD_DEG:
            continue
        if max(g["mm0"], g["mm1"]) > GAP_MAX_RANGE_MM:
            continue
        w = g["width_mm"]
        if 0 < w < threshold_mm and (best == 0 or w < best):
            best = w
    return best


SECTOR_DEG = 45          # how far either side of straight-abeam to look, for angled walls
SLICE_MM = 300           # a return must be within this much of abeam to bound the gap
MAX_SIDE_MM = 2500       # a return further to the side than this is not what Scout is passing
FRONT_DEG = 25           # the cone Scout calls "ahead"
FRONT_CLEAR_MM = 600     # the way ahead must be at least this open for a gap to be a passage
PINCH_OPEN = 1.4         # a gap under this multiple of the limit opens a pinch
MIN_PINCH_SAMPLES = 4    # a pinch seen on fewer scans than this was a glimpse, not a doorway
DROPOUT_DEG = 15         # the window straight out from Scout used to judge how well a side is seen
MAX_DROPOUT = 0.6        # a side this empty may be hiding a nearer surface that did not reflect


def clearance(scan, sector_deg=SECTOR_DEG):
    """The width of the opening Scout is passing through, from one 360-entry scan.

    Measured across a thin slice perpendicular to the direction of travel: of the returns that lie
    beside Scout, take the one closest to its centre line on the left and the one closest on the
    right. Their sum is the gap.

    Measuring perpendicular matters. The obvious version -- nearest return with a positive sideways
    offset, nearest with a negative one, anywhere ahead -- makes a wall straight in front count as
    both bounds at once, because a point dead ahead has a sideways offset of nearly zero. That
    reports a doorway a few millimetres wide every time Scout faces a wall. A return ahead is an
    obstruction, not a narrow gap, and the sectors here leave it out by construction.

    Returns (width_mm, left_point, right_point) in the robot frame, or (0, None, None) when the
    path is not bounded on both sides -- an open room has no clearance to report.
    """
    if not scan or len(scan) != 360:
        return 0, None, None

    # A passage is something Scout could drive through. Facing into a corner, the wall it has been
    # following and the wall across the corner both land in the slice and read as a narrow doorway;
    # every corner of every room would fail. If Scout cannot go forward, it is not passing through
    # anything, so there is no clearance to report.
    for d in range(-FRONT_DEG, FRONT_DEG + 1):
        mm = scan[d % 360]
        if 0 < mm < FRONT_CLEAR_MM:
            return 0, None, None

    def side_of(centre):
        # Judge how well this side is seen only from the bearings straight out from Scout. Any
        # surface bounding the gap, near or far, has to answer there. Counting no-returns across
        # the whole sector instead would condemn a perfectly good distant wall, because the wide
        # bearings only ever catch something when the surface is close.
        seen = dropouts = 0
        for d in range(centre - DROPOUT_DEG, centre + DROPOUT_DEG + 1):
            seen += 1
            if scan[d % 360] <= 0:
                dropouts += 1
        if seen and dropouts / seen > MAX_DROPOUT:
            return None          # too little came back to trust the nearest thing on this side

        best = None
        for d in range(centre - sector_deg, centre + sector_deg + 1):
            mm = scan[d % 360]
            if mm <= 0:
                continue
            a = math.radians(d % 360)
            along, off = mm * math.cos(a), abs(mm * math.sin(a))
            # Beside Scout, not merely off to one side. A point well behind or well ahead still has
            # a sideways offset, so without this the wall Scout has just driven away from -- a few
            # hundred millimetres back and off to the left -- becomes the left-hand bound of the
            # doorway it is currently in, and the gap reads far narrower than it is.
            if abs(along) > SLICE_MM or off > MAX_SIDE_MM:
                continue
            if best is None or off < best[0]:
                best = (off, (along, mm * math.sin(a)))
        return best

    left, right = side_of(90), side_of(270)
    if left is None or right is None:
        return 0, None, None
    return int(round(left[0] + right[0])), left[1], right[1]


class Audit:
    """Turns a stream of clearance readings into one pass/fail per pinch point."""

    def __init__(self, cfg):
        self.cfg = cfg               # the live runtime config dict, shared with the server
        self._pinch = None           # {"min", "t0", "at", "n"} while a pinch point is open
        self._gap_at = None          # when a qualifying gap was last seen ahead

    def reset(self):
        self._pinch = None
        self._gap_at = None

    def step(self, now, width_mm, place, gaps=()):
        """Feed one clearance reading and the gaps seen in the same scan.

        Two sources, one pinch. `width_mm` is the opening Scout is inside right now; a
        `see_through` gap ahead is one it can see but has not reached. Taking both means a doorway
        Scout looks at and then drives through is a single event carrying the narrower of the two,
        rather than one event for looking and another for arriving.

        `place` maps a robot-frame point to the room frame, or is None when there is no pose.
        Returns a list of events (0 or 1)."""
        limit = float(self.cfg["width_limit_mm"])
        ahead = audit_width_from_gaps(gaps, PINCH_OPEN * limit)
        if ahead:
            self._gap_at = now
            if width_mm == 0 or ahead < width_mm:
                width_mm = ahead
        # A gap is found afresh in every rotation, and one rotation in a few will miss it as Scout
        # turns or a sample drops. Treating that single blank scan as "the doorway is gone" would
        # close the pinch and fire a second event for the same doorway, so a gap stays counted as
        # seen for a moment after its last sighting.
        gap_recent = self._gap_at is not None and now - self._gap_at < GAP_LATCH_S
        if self._pinch is None:
            if 0 < width_mm < PINCH_OPEN * limit:
                self._pinch = {"min": width_mm, "t0": now, "at": place, "n": 1}
            return []

        if 0 < width_mm:
            self._pinch["n"] += 1
            if width_mm < self._pinch["min"]:
                self._pinch["min"] = width_mm
                self._pinch["at"] = place    # remember where the narrowest point was, not where it ended
        open_enough = (width_mm == 0 or width_mm >= PINCH_OPEN * limit) and not gap_recent
        if not open_enough and now - self._pinch["t0"] < PINCH_MAX_S:
            return []

        value, at, n = self._pinch["min"], self._pinch["at"], self._pinch["n"]
        self._pinch = None
        self._gap_at = None
        if n < MIN_PINCH_SAMPLES:
            # One or two scans of a narrow reading is Scout clipping a corner or a stray return,
            # not a doorway it went through. Saying nothing beats crying wolf at every corner.
            return []
        ev = {"kind": "width_fail" if value < limit else "width_pass",
              "value": int(value), "unit": "mm", "limit": round(limit, 1),
              "between": at[2] if at else "unknown"}
        # The width itself is a real measurement whether or not Scout knows where it is standing,
        # so the verdict is still reported -- just without a position to pin it to on the map.
        if at:
            ev["x_mm"], ev["y_mm"] = at[0], at[1]
        return [ev]
