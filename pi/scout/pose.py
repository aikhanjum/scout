"""Where Scout is, from the lidar alone (PROTOCOL.md section 4).

Scout has no odometry and no IMU, so position comes out of the scan itself. The test space is a
rectangular room, which makes this tractable without scan matching: the convex hull of one scan is
the room, and the minimum-area rectangle of that hull gives the walls' orientation and extent
directly. Read the robot's pose off that rectangle and there is nothing to accumulate, so there is
nothing to drift.

The room frame is locked on the first good fit, with x along the longer wall. Later fits are matched
to it by picking whichever rotation is most continuous with the last pose. Position does most of the
work there: between scans at 5.5 Hz Scout moves about 80 mm, so a wrong 90-degree reading lands
metres away and is never the cheapest. Heading alone cannot separate the rotations mid-corner.

A wrong pose corrupts the map permanently and a missing one costs nothing, so every check here
fails closed: no fit, no pose.
"""
import logging
import math

log = logging.getLogger("scout.pose")

MIN_POINTS = 60          # a scan with fewer returns than this is not a room
MIN_SIDE_MM = 800        # smaller than this is furniture, not a room
MAX_SIDE_MM = 15000      # bigger than this is the A1 seeing through a doorway
EDGE_TOL_MM = 120        # how near a wall a point must be to count as being on it
ANGLE_STEP_DEG = 1       # orientation sweep resolution; the room repeats every 90 degrees
TRIMS = (0.0, 0.02, 0.08)   # quantiles at which to try reading a wall off the projections. 0.0 is
                            # the true extreme, which is the only thing that finds a wall reduced to
                            # a handful of returns by an obstacle standing in front of it.
MIN_ON_EDGE = 0.55       # this fraction of points must lie on the rectangle, or it is not a room
JUMP_PER_DEG_MM = 20.0   # mm of position jump that costs as much as one degree of heading change
MAX_JUMP_COST = 90.0     # above this, no rotation matches the lock and the pose is reported missing
RELOCK_AFTER = 20        # consecutive unmatched scans before re-acquiring the room frame
LOST_BUDGET_PER_SCAN = 4.0  # extra cost allowed per scan spent lost (about 80 mm of travel)
MAX_LOST_BUDGET = 260.0  # ceiling on that, so a long loss never waves a bad fit through
SIZE_REJECT_MM = 600     # total size mismatch above which this scan is not the whole room
MIN_VISIBLE = 0.5        # fraction of a wall-to-wall span that must be visible to rebuild the rest
MIN_WALL_POINTS = 8      # how much more populated one extreme must be to be called the real wall
HOLD_SCANS = 40          # scans a stopped robot may coast on its last pose (about 7 s at 5.5 Hz)
MIN_WALL_STANDOFF_MM = 100   # a wall must be at least this far from the lidar, which sits inside
MIN_WALL_SAMPLES = 6         # a wall needs this many returns; fewer is a stray, however far away
TIE_FRACTION = 0.04          # scores within this fraction of each other are a tie, broken by area


def scan_points(scan):
    """[(x, y), ...] in the robot frame, from the 360-entry scan. 0 means no return."""
    pts = []
    for i, mm in enumerate(scan):
        if mm > 0:
            a = math.radians(i)
            pts.append((mm * math.cos(a), mm * math.sin(a)))
    return pts


def convex_hull(pts):
    """Andrew's monotone chain. Returns the hull counter-clockwise, without repeating the first point."""
    p = sorted(set(pts))
    if len(p) < 3:
        return p
    def half(points):
        out = []
        for q in points:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (q[1] - ay) - (by - ay) * (q[0] - ax) > 0:
                    break
                out.pop()
            out.append(q)
        return out
    return half(p)[:-1] + half(reversed(p))[:-1]


def min_area_rect(hull):
    """Rotating calipers. Returns (cx, cy, theta, a, b): centre, the angle of the rectangle's own
    x axis in the robot frame, and the two half-extents. The minimum-area rectangle of a convex
    polygon always has a side flush with one of the polygon's edges, so trying every edge is exact."""
    best = None
    n = len(hull)
    for i in range(n):
        (x0, y0), (x1, y1) = hull[i], hull[(i + 1) % n]
        theta = math.atan2(y1 - y0, x1 - x0)
        c, s = math.cos(-theta), math.sin(-theta)
        us = [x * c - y * s for x, y in hull]
        vs = [x * s + y * c for x, y in hull]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        area = (u1 - u0) * (v1 - v0)
        if best is None or area < best[0]:
            # the centre back in the robot frame
            mu, mv = (u0 + u1) / 2, (v0 + v1) / 2
            cx = mu * math.cos(theta) - mv * math.sin(theta)
            cy = mu * math.sin(theta) + mv * math.cos(theta)
            best = (area, cx, cy, theta, (u1 - u0) / 2, (v1 - v0) / 2)
    return best[1:]


def _hull_rect(pts, tol):
    """The old fit, kept because it is still the best answer in a closed room.

    The minimum-area rectangle of the convex hull. Every return in a closed room is inside it, so
    the hull is the room, and because it is built from the outermost points it still finds a wall
    that an obstacle has reduced to a handful of returns. Its weakness is the same property: in an
    open arena the outermost points are whatever escaped through a gap, and the rectangle is
    meaningless. Scored against the peak fit, not trusted blindly.
    """
    hull = convex_hull(pts)
    if len(hull) < 3:
        return None
    cx, cy, th, a, b = min_area_rect(hull)
    c, s = math.cos(-th), math.sin(-th)
    u0, u1, v0, v1 = -a, a, -b, b
    us = [(x - cx) * c - (y - cy) * s for x, y in pts]
    vs = [(x - cx) * s + (y - cy) * c for x, y in pts]
    on = sum(1 for u, v in zip(us, vs)
             if min(abs(u - u0), abs(u - u1), abs(v - v0), abs(v - v1)) <= tol)
    return on, (2 * a) * (2 * b), th, cx + u0 * math.cos(th) - v0 * math.sin(th), a, b, cx, cy


def _hist(vals, tol, weights=None):
    """Bin projections, optionally accumulating a weight instead of a count."""
    h = {}
    if weights is None:
        for v in vals:
            b = int(v // tol)
            h[b] = h.get(b, 0) + 1
    else:
        for v, w in zip(vals, weights):
            b = int(v // tol)
            h[b] = h.get(b, 0.0) + w
    return h


def _near(h, pos, tol):
    """How many returns sit within a bin of `pos`. Bins are tol wide, so three of them span it."""
    b = int(pos // tol)
    return h.get(b - 1, 0) + h.get(b, 0) + h.get(b + 1, 0)


def _straddles(a, b):
    """The lidar sits inside the room, so its two walls lie either side of it. Free, and strong:
    without it the best-supported pair can be two surfaces on the same side -- the wall Scout is
    hugging and a table beyond it -- putting the robot outside its own room."""
    return a < -MIN_WALL_STANDOFF_MM and b > MIN_WALL_STANDOFF_MM and MIN_SIDE_MM <= b - a <= MAX_SIDE_MM


def _wall_candidates(counts, weights, tol, n):
    """Every plausible reading of where one pair of opposite walls lies.

    There is no single right rule, because Scout's two environments fail in opposite directions.

    In a closed room every return is inside it, so the extremes of the projection are the walls --
    and they still find a wall that an obstacle has reduced to a handful of returns, which is the
    case that matters. The simulated course has exactly that: a ramp stands against the far wall
    and leaves eleven returns reaching the wall itself. Peaks cannot see past the ramp.

    In an open arena -- four tables on their sides, say -- returns escape through every gap and the
    extremes land out in the room beyond. There the walls are peaks and the extremes are noise.

    So both readings are offered and the caller keeps whichever better explains the scan.
    """
    out = []
    bs = sorted(counts)
    if not bs:
        return out
    for trim in TRIMS:
        target, acc, lo, hi = trim * n, 0, bs[0], bs[-1]
        for b in bs:
            acc += counts[b]
            if acc > target:
                lo = b
                break
        acc = 0
        for b in reversed(bs):
            acc += counts[b]
            if acc > target:
                hi = b
                break
        pair = ((lo + 0.5) * tol, (hi + 0.5) * tol)
        if _straddles(*pair) and pair not in out:
            out.append(pair)

    # peaks, by range-weighted support. A lidar samples at a fixed angular step, so a surface
    # returns points in inverse proportion to distance: counting them picks whatever is nearest.
    # Weighting each return by its range cancels that, so a wall counts for its length. The sample
    # floor keeps two strays nine metres away from outvoting a wall.
    scored = {b: w for b, w in weights.items() if _near(counts, (b + 0.5) * tol, tol) >= MIN_WALL_SAMPLES}
    top = sorted(scored, key=lambda b: -scored[b])[:12]
    best = None
    for lo in top:
        for hi in top:
            pair = ((lo + 0.5) * tol, (hi + 0.5) * tol)
            if not _straddles(*pair):
                continue
            tot = scored[lo] + scored[hi]
            if best is None or tot > best[0]:
                best = (tot, pair)
    if best and best[1] not in out:
        out.append(best[1])
    return out


def fit_room(pts, step_deg=ANGLE_STEP_DEG, tol=EDGE_TOL_MM):
    """Find the room's four walls. Returns (cx, cy, theta, a, b, on_edge_fraction), or None.

    Sweeps the orientation; at each one, tries every reading of the walls and keeps whichever puts
    most of the scan on the rectangle it implies.

    Ties are broken towards the larger rectangle, and that is not cosmetic. A room contains its own
    furniture, so a rectangle drawn across the face of a cabinet standing against the far wall
    explains the scan about as well as the true room does -- on the simulated course, 84% against
    83% -- while putting the wall a metre closer than it is. Preferring the larger reading when the
    evidence is level is what says the cabinet is in the room rather than the edge of it.

    This replaces fitting the convex hull, which is defined by its most distant points: the few
    longest sight lines set the rectangle and everything else sits inside it. On a real scan taken
    inside an arena of four tables, hull fitting put 3% of returns on a 12.5 x 7.5 m rectangle.
    """
    n = len(pts)
    rs = [max(1.0, math.hypot(x, y)) for x, y in pts]
    best = None
    margin = TIE_FRACTION * n
    for deg in range(0, 90, step_deg):
        th = math.radians(deg)
        c, s = math.cos(-th), math.sin(-th)
        us = [x * c - y * s for x, y in pts]
        vs = [x * s + y * c for x, y in pts]
        hu, hv = _hist(us, tol), _hist(vs, tol)
        wu, wv = _hist(us, tol, rs), _hist(vs, tol, rs)
        cand_u = _wall_candidates(hu, wu, tol, n)
        cand_v = _wall_candidates(hv, wv, tol, n)
        for u0, u1 in cand_u:
            nu = _near(hu, u0, tol) + _near(hu, u1, tol)
            for v0, v1 in cand_v:
                on = nu + _near(hv, v0, tol) + _near(hv, v1, tol)
                area = (u1 - u0) * (v1 - v0)
                if best is None or on > best[0] + margin or (on > best[0] - margin and area > best[1]):
                    best = (on, area, th, u0, u1, v0, v1)
    hull_fit = _hull_rect(pts, tol)
    if hull_fit is not None:
        on_h, area_h, th_h, _unused, a_h, b_h, cx_h, cy_h = hull_fit
        if best is None or on_h > best[0] + margin or (on_h > best[0] - margin and area_h > best[1]):
            # the hull answer wins: hand it back directly, already centred
            return cx_h, cy_h, th_h, a_h, b_h, min(1.0, on_h / n)
    if best is None:
        return None
    on, _area, th, u0, u1, v0, v1 = best

    # The search works on bins, so every wall lands on a bin centre and is up to half a bin out.
    # Settle each one onto the mean of the returns actually lying on it: the walls are the most
    # precise thing in the scan, and rounding them to 120 mm throws that away for nothing. Worth
    # about a hundred millimetres of pose error on the simulated course.
    c, s = math.cos(-th), math.sin(-th)
    us = [x * c - y * s for x, y in pts]
    vs = [x * s + y * c for x, y in pts]
    def settle(vals, wall):
        on_wall = [v for v in vals if abs(v - wall) <= tol]
        return sum(on_wall) / len(on_wall) if len(on_wall) >= MIN_WALL_SAMPLES else wall
    u0, u1 = settle(us, u0), settle(us, u1)
    v0, v1 = settle(vs, v0), settle(vs, v1)

    mu, mv = (u0 + u1) / 2, (v0 + v1) / 2
    cx = mu * math.cos(th) - mv * math.sin(th)
    cy = mu * math.sin(th) + mv * math.cos(th)
    return cx, cy, th, (u1 - u0) / 2, (v1 - v0) / 2, min(1.0, on / n)


def _repair_occlusion(us, vs, room):
    """Put back a wall that an obstacle is hiding.

    Once the room's size is locked, a scan whose rectangle comes out too small is not a smaller
    room -- it is the same room with a wall behind a filing cabinet. Throwing it away costs whole
    seconds of pose every time Scout squeezes past furniture. Instead: the axis that is short has
    one true wall and one hidden one, and the true wall is the one with points sitting on it, so
    slide the hidden side out to the known room width.

    Returns (u0, u1, v0, v1) or None when too little is visible to say.
    """
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    su, sv = u1 - u0, v1 - v0

    def fix(lo, hi, vals, expected):
        span = hi - lo
        if span >= expected - EDGE_TOL_MM:
            return lo, hi                       # nothing hidden, or the fit is already generous
        if span < expected * MIN_VISIBLE:
            return None                         # both walls hidden: this is not a measurement
        near_lo = sum(1 for x in vals if x - lo <= EDGE_TOL_MM)
        near_hi = sum(1 for x in vals if hi - x <= EDGE_TOL_MM)
        if abs(near_lo - near_hi) < MIN_WALL_POINTS:
            return None                         # cannot tell which side is the real wall
        return (lo, lo + expected) if near_lo > near_hi else (hi - expected, hi)

    # which locked dimension belongs to which axis: whichever pairing needs less repair
    ea, eb = room if abs(su - room[0]) + abs(sv - room[1]) <= abs(su - room[1]) + abs(sv - room[0]) else (room[1], room[0])
    fu, fv = fix(u0, u1, us, ea), fix(v0, v1, vs, eb)
    return None if fu is None or fv is None else (*fu, *fv)


def _on_edge_fraction(pts, cx, cy, theta, a, b):
    """How much of the scan actually lies on the rectangle's boundary. A room scores high; a scan
    with one wall and open space, or a cluttered non-rectangular space, scores low."""
    c, s = math.cos(-theta), math.sin(-theta)
    on = 0
    for x, y in pts:
        dx, dy = x - cx, y - cy
        u, v = dx * c - dy * s, dx * s + dy * c
        if min(abs(abs(u) - a), abs(abs(v) - b)) <= EDGE_TOL_MM:
            on += 1
    return on / len(pts)


class Pose:
    """Tracks the room frame across scans. `ok` is false whenever the current scan did not fit."""

    def __init__(self):
        self.ok = False
        self.x = 0.0
        self.y = 0.0
        self.heading = 0.0       # degrees, room frame, positive counter-clockwise
        self.room = None         # (w_mm, l_mm), the locked room size
        self._locked = False
        self._lost = 0
        self._held = 0
        self.relocked = False

    def clear(self):
        self.__init__()

    @property
    def _hold(self):
        """The pose to fall back on while Scout is stopped, or None when there is nothing to hold.

        Requires the pose to have still been good on the previous scan. Otherwise Scout could lose
        the fit while driving, coast on for metres, stop, and then resurrect a pose from wherever it
        used to be -- which looks perfectly confident and is entirely wrong.
        """
        if not (self._locked and self.ok and self._held < HOLD_SCANS):
            return None
        return (self.x, self.y, self.heading)

    def _fail(self, held):
        """No fit this scan. Keep a stationary robot's last pose; otherwise report none."""
        if held is None:
            self._held = 0
            self.ok = False
            return False
        self.x, self.y, self.heading = held
        self._held += 1
        self.ok = True
        return True

    def update(self, scan, moving=True):
        """Fit this scan and move the pose. Returns True when the pose is valid.

        `moving` false means Scout's wheels are stopped. A stationary robot's last good pose is
        still its pose, so a failed fit is held rather than dropped. This matters: Scout loses the
        fit precisely when it is parked close to something big enough to hide a wall, which is the
        same moment it has stopped to photograph that thing and needs somewhere to pin it.
        """
        held = self._hold if not moving else None
        if not scan or len(scan) != 360:
            return self._fail(held)
        pts = scan_points(scan)
        if len(pts) < MIN_POINTS:
            return self._fail(held)
        fit = fit_room(pts)
        if fit is None:
            return self._fail(held)
        cx, cy, theta, a, b, on_edge = fit
        if not (MIN_SIDE_MM / 2 <= a <= MAX_SIDE_MM / 2 and MIN_SIDE_MM / 2 <= b <= MAX_SIDE_MM / 2):
            return self._fail(held)
        if on_edge < MIN_ON_EDGE:
            return self._fail(held)

        # A rectangle that comes out smaller than the locked room means a wall is hidden behind
        # furniture. Rebuild it from the wall that is visible rather than losing the pose.
        if self._locked:
            c, s = math.cos(-theta), math.sin(-theta)
            us = [(x - cx) * c - (y - cy) * s for x, y in pts]
            vs = [(x - cx) * s + (y - cy) * c for x, y in pts]
            fixed = _repair_occlusion(us, vs, self.room)
            if fixed is None:
                self._lost += 1
                return self._fail(held)
            u0, u1, v0, v1 = fixed
            mu, mv = (u0 + u1) / 2, (v0 + v1) / 2
            cx += mu * math.cos(theta) - mv * math.sin(theta)
            cy += mu * math.sin(theta) + mv * math.cos(theta)
            a, b = (u1 - u0) / 2, (v1 - v0) / 2

        # How this rectangle can be read as the locked room: each 90-degree rotation swaps the
        # extents and moves the origin to the next corner. All four are scored; the ones that put
        # the short wall where the long wall belongs are thrown out by the size term below.
        # On the very first fit there is nothing to match against, so the frame is defined here:
        # x along the longer wall (section 4). Without this the room frame would come out differently
        # depending on which wall Scout happened to be facing when it started.
        first = (0 if a >= b else 1) if not self._locked else None
        best = None
        for k in (range(4) if first is None else (first,)):
            th = theta + k * math.pi / 2
            ea, eb = (a, b) if k % 2 == 0 else (b, a)
            # the corner that is the room origin, in the robot frame
            ux, uy = math.cos(th), math.sin(th)
            vx, vy = -math.sin(th), math.cos(th)
            ox, oy = cx - ea * ux - eb * vx, cy - ea * uy - eb * vy
            # the robot sits at the robot frame's origin, so its room coordinates are -corner
            px, py = -(ox * ux + oy * uy), -(ox * vx + oy * vy)
            heading = -math.degrees(th)
            heading = (heading + 180) % 360 - 180
            cand = (px, py, heading, 2 * ea, 2 * eb)
            if not self._locked:
                best = cand
                break
            # Match the lock on continuity and on shape. Position carries most of the weight:
            # between scans at 5.5 Hz Scout can move about 80 mm, so a wrong reading throws the
            # position metres away and is never the cheapest. Heading alone cannot tell the four
            # apart while Scout is turning a corner. The size term rules out the two rotations that
            # would stand the room on its side, which matters most in a nearly square room where
            # this scan's noisy extents cannot be trusted to say which wall is the long one.
            # Standing the room on its side is not a reading of the room, it is a different room.
            # A hard gate, not a penalty: after a long loss the position term is weak, and a soft
            # penalty lets a 90-degree flip buy its way past and silently mirror the whole map.
            if abs(2 * ea - self.room[0]) + abs(2 * eb - self.room[1]) > SIZE_REJECT_MM:
                continue
            dh = abs((heading - self.heading + 180) % 360 - 180)
            cost = dh + math.hypot(px - self.x, py - self.y) / JUMP_PER_DEG_MM
            if best is None or cost < best[0]:
                best = (cost, *cand)

        if not self._locked:
            px, py, heading, w, l = best
            self.room = (round(w), round(l))
            self._locked = True
        else:
            if best is None:                     # no rotation is the right shape for this room
                self._lost += 1
                return self._fail(held)
            cost, px, py, heading, w, l = best
            # This rectangle is supposed to be the room, and the room's size is already known. When
            # it is not, the scan did not see the whole room -- an obstacle is occluding a wall --
            # and the corner it measured from is the wrong corner, which lands the pose a metre out
            # while still looking self-consistent. Refuse it.
            if abs(w - self.room[0]) + abs(l - self.room[1]) > SIZE_REJECT_MM:
                self._lost += 1
                return self._fail(held)
            # The budget grows with every scan since the last accepted pose: after a second of no
            # fits Scout really has moved, so the honest jump is bigger than it would be at 5.5 Hz.
            # The budget grows by what Scout could actually have driven while it was lost,
            # not without bound: at cruise it covers about 80 mm per scan.
            if cost > min(MAX_JUMP_COST + LOST_BUDGET_PER_SCAN * self._lost, MAX_LOST_BUDGET):
                # Nothing matches the lock. Scout was picked up, or this is not the same room, so
                # report no pose rather than a confident wrong one.
                self._lost += 1
                if self._lost < RELOCK_AFTER:
                    return self._fail(held)
                # Give up and re-acquire. The new frame has no relation to the old one, so every
                # point already on the map is now in the wrong frame: say so and let the server
                # throw the map away. Silently re-basing would corrupt the map instead.
                log.warning("pose lost for %d scans (cost %.0f): re-acquiring, the map is void", self._lost, cost)
                self.relocked = True
                self.room = (round(w), round(l))
            self._lost = 0

        self.x, self.y, self.heading, self.ok = px, py, heading, True
        self._held = 0
        return True

    def take_relock(self):
        """True once after the room frame was re-acquired. The caller must clear the map."""
        was, self.relocked = self.relocked, False
        return was

    def snapshot(self):
        """An immutable read for the server thread."""
        return (self.ok, round(self.x), round(self.y), round(self.heading, 1), self.room)
