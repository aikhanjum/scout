# Vendored from ~/dev/lidar-gaps/lidar_gaps/gaps.py on 2026-09-19. Edit there first, then copy here.

"""Gap finder for one rotation of 2D lidar points.

Input is one full rotation as (angle_deg, distance_mm) pairs in any order.
Distance 0 (or negative) means "no return" and is ignored, which is what the
RPLIDAR reports for out-of-range or non-reflective targets.

A gap is an opening between two wall points. The wall is first split into
segments wherever two angle-adjacent points are further apart than
`min_gap_mm` (a missing return or a depth jump). A gap then runs from the
end of one segment to the start of a later segment, skipping any segments
in between that are entirely farther away than both ends. That way a
doorway with a far wall visible through it is reported once, as the
opening between its two door frames, not as two depth-jump edges.

Width is the straight-line distance between the two frame points, in mm.
"""
import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

Point = Tuple[float, float]  # (angle_deg, distance_mm)


SEE_THROUGH = "see_through"   # something was seen past the opening: it is really open
STEP = "step"                 # the two edges are adjacent samples: a range step, not an arc
UNVERIFIED = "unverified"     # the arc is all no-returns: an opening and a dead surface look alike


@dataclass(frozen=True)
class Gap:
    start_angle_deg: float
    start_mm: float
    end_angle_deg: float
    end_mm: float
    width_mm: float
    span_deg: float = 0.0     # angular width of the arc between the two edges
    samples: int = 0          # samples the sensor took inside that arc
    dropouts: int = 0         # how many of them came back with no return
    evidence: str = STEP      # SEE_THROUGH | STEP | UNVERIFIED, see classify below

    @property
    def mid_angle_deg(self) -> float:
        """Bearing of the gap centre, 0..360, correct across the 0/360 wrap."""
        span = (self.end_angle_deg - self.start_angle_deg) % 360.0
        return (self.start_angle_deg + span / 2.0) % 360.0


def _xy(p: Point) -> Tuple[float, float]:
    r = math.radians(p[0])
    return p[1] * math.cos(r), p[1] * math.sin(r)


def _chord(p: Point, q: Point) -> float:
    (ax, ay), (bx, by) = _xy(p), _xy(q)
    return math.hypot(ax - bx, ay - by)


def _ring(wall: Sequence[Point], start: int, end: int) -> List[Point]:
    """Points from index start to end inclusive, wrapping around the ring."""
    n = len(wall)
    out, i = [], start % n
    while True:
        out.append(wall[i])
        if i == end % n:
            return out
        i = (i + 1) % n


def _inside(all_pts: Sequence[Point], a0: float, a1: float) -> List[Point]:
    """Every sample strictly inside the arc from a0 forward to a1."""
    span = (a1 - a0) % 360.0
    return [p for p in all_pts if 0.0 < (p[0] - a0) % 360.0 < span]


def _classify(all_pts: Sequence[Point], start: Point, end: Point):
    """What evidence is there that this gap is a real opening?

    A lidar reports a no-return exactly the same way whether the beam went
    through an opening and hit nothing, or hit glass, a mirror, matte black, or
    a surface at a grazing angle. One rotation cannot separate those two, so an
    arc of pure no-returns is reported as UNVERIFIED rather than as a measured
    opening. The one thing that does settle it is seeing something through the
    gap: a return inside the arc, farther away than both edges, means the beam
    got past them, which a solid surface would not allow."""
    span = (end[0] - start[0]) % 360.0
    inside = _inside(all_pts, start[0], end[0])
    dropouts = sum(1 for _, d in inside if d <= 0)
    beyond = max(start[1], end[1])
    if not inside:
        evidence = STEP
    elif any(d > beyond for _, d in inside):
        evidence = SEE_THROUGH
    elif dropouts == len(inside):
        evidence = UNVERIFIED
    else:
        evidence = STEP
    return span, len(inside), dropouts, evidence


def find_gaps(points: Iterable[Point], min_gap_mm: float = 300.0) -> List[Gap]:
    """Return every gap between wall points in one rotation, sorted by bearing.

    points:     (angle_deg, distance_mm) pairs, one rotation, any order.
    min_gap_mm: two angle-adjacent points further apart than this (straight line)
                are a break in the wall. Also the smallest gap reported.

    Each break is a gap. Where the sensor can see past an opening -- the range
    jumps outward at one edge and back inward at the other, with only farther
    points in between -- the two breaks are one opening, and it is measured
    between the two near edges rather than reported as two depth jumps. That
    pairing is strictly local: a break is only ever merged with the one that
    follows it, so a single very close reading cannot swallow the rotation.
    """
    all_pts = sorted((a % 360.0, float(d)) for a, d in points)
    wall = [p for p in all_pts if p[1] > 0]
    n = len(wall)
    if n < 2:
        return []
    breaks = [i for i in range(n) if _chord(wall[i], wall[(i + 1) % n]) > min_gap_mm]
    m = len(breaks)
    if m == 0:
        return []

    def edges(k: int) -> Tuple[Point, Point]:
        i = breaks[k]
        return wall[i], wall[(i + 1) % n]

    # A break whose range steps outward may be the near edge of an opening; pair it
    # with the next break if that one steps back inward over purely farther points.
    consumed = set()
    merges = {}
    for k in range(m):
        near, far = edges(k)
        if m < 2 or far[1] <= near[1]:
            continue
        k2 = (k + 1) % m
        if k2 == k:
            continue
        far2, near2 = edges(k2)
        if near2[1] >= far2[1]:
            continue
        between = _ring(wall, breaks[k] + 1, breaks[k2])
        if min(d for _, d in between) > max(near[1], near2[1]):
            merges[k] = near2
            consumed.add(k2)

    gaps: List[Gap] = []
    for k in range(m):
        if k in consumed:
            continue
        start, end = edges(k)[0], merges.get(k) or edges(k)[1]
        w = _chord(start, end)
        if w >= min_gap_mm:
            span, samples, dropouts, evidence = _classify(all_pts, start, end)
            gaps.append(Gap(start[0], start[1], end[0], end[1], w,
                            span_deg=span, samples=samples, dropouts=dropouts,
                            evidence=evidence))
    gaps.sort(key=lambda g: g.start_angle_deg)
    return gaps
