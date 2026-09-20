"""Where Scout is, from the lidar alone (PROTOCOL.md section 4).

Scout has no odometry and no IMU, so position comes out of the scan itself. The test space is a
rectangular room, which makes this tractable without scan matching: the convex hull of one scan is
the room, and the minimum-area rectangle of that hull gives the walls' orientation and extent
directly. Read the robot's pose off that rectangle and there is nothing to accumulate, so there is
nothing to drift.

The room frame is locked on the first good fit, with x along the longer wall. Later fits are matched
to it by picking whichever rotation is most continuous with the last pose. Position does most of the
work there: between scans at the A2M8's 10 Hz Scout moves about 40 mm, so a wrong 90-degree reading
lands metres away and is never the cheapest. Heading alone cannot separate the rotations mid-corner.

**Build the room oblong.** A square room has a failure this file cannot fix. Telling the four
rotations apart leans on the room's shape -- stand a 4 x 5 m room on its side and the extents stop
matching -- and when the two sides are equal that cue is gone. Then, if Scout turns roughly 90
degrees while it cannot see (someone leans over the lidar mid-turn), the scan that comes back is
*identical* to the one it would have produced had it never turned. Both readings fit perfectly;
nothing in a horizontal scan of an empty rectangle separates them, and furniture does not help
because the pose is read off the fitted rectangle alone. Measured: a square room comes back from
that 90 degrees out, an oblong one comes back exact. No gate fixes it -- the deceptive reading is
the one that looks *more* continuous, not less. Make the two sides differ by more than
SIZE_REJECT_MM and the problem disappears; `_warn_if_square` says so at lock time.

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
MIN_ON_EDGE = 0.55       # this fraction of points must lie on the rectangle, or it is not a room
JUMP_PER_DEG_MM = 20.0   # mm of position jump that costs as much as one degree of heading change
MAX_JUMP_COST = 90.0     # above this, no rotation matches the lock and the pose is reported missing
RELOCK_AFTER = 20        # consecutive unmatched scans before re-acquiring the room frame
LOST_BUDGET_PER_SCAN = 4.0  # extra cost allowed per scan spent lost (about 80 mm of travel)
MAX_LOST_BUDGET = 260.0  # ceiling on that, so a long loss never waves a bad fit through
SIZE_REJECT_MM = 600     # total size mismatch above which this scan is not the whole room
MIN_VISIBLE = 0.5        # fraction of a wall-to-wall span that must be visible to rebuild the rest
MIN_WALL_POINTS = 8      # how much more populated one extreme must be to be called the real wall
HOLD_SCANS = 40          # scans a stopped robot may coast on its last pose (about 4 s at 10 Hz)


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

    def _warn_if_square(self):
        """Announce a room that cannot tell its own quarter turns apart.

        A missing device is logged loudly at startup (rule 9); a room that will silently lie about
        which way Scout is facing deserves the same. This is the one pose failure that is not
        fixable in software -- see the module docstring -- so the fix is a tape measure and a
        shifted wall, and someone has to be told while there is still time to move it.
        """
        w, l = self.room
        if 2 * abs(w - l) <= SIZE_REJECT_MM:
            log.warning("ROOM IS SQUARE (%d x %d mm): if Scout turns about 90 degrees while the "
                        "pose is lost, it comes back a quarter turn out and the map is wrong with "
                        "no warning. Move a wall so the sides differ by more than %d mm.",
                        w, l, SIZE_REJECT_MM // 2)

    @staticmethod
    def _read_as(cx, cy, theta, a, b, k):
        """Read the fitted rectangle as the room, turned through k quarter turns.

        Each quarter turn swaps the extents and moves the room's origin to the next corner, so the
        same rectangle yields four different poses. Returns (x, y, heading_deg, w, l) for Scout,
        which sits at the robot frame's origin.
        """
        th = theta + k * math.pi / 2
        ea, eb = (a, b) if k % 2 == 0 else (b, a)
        ux, uy = math.cos(th), math.sin(th)
        vx, vy = -math.sin(th), math.cos(th)
        ox, oy = cx - ea * ux - eb * vx, cy - ea * uy - eb * vy     # the room origin corner
        px, py = -(ox * ux + oy * uy), -(ox * vx + oy * vy)         # so Scout is at minus that
        return px, py, (-math.degrees(th) + 180) % 360 - 180, 2 * ea, 2 * eb

    def update(self, scan, moving=True):
        """Fit this scan and move the pose. Returns True when the pose is valid.

        `moving` false means Scout's wheels are stopped. A stationary robot's last good pose is
        still its pose, so a failed fit is held rather than dropped. This matters: Scout loses the
        fit precisely when it is parked close to something big enough to hide a wall, which is the
        same moment the map needs somewhere to pin that thing.
        """
        held = self._hold if not moving else None
        if not scan or len(scan) != 360:
            return self._fail(held)
        pts = scan_points(scan)
        if len(pts) < MIN_POINTS:
            return self._fail(held)
        hull = convex_hull(pts)
        if len(hull) < 3:
            return self._fail(held)
        cx, cy, theta, a, b = min_area_rect(hull)
        if not (MIN_SIDE_MM / 2 <= a <= MAX_SIDE_MM / 2 and MIN_SIDE_MM / 2 <= b <= MAX_SIDE_MM / 2):
            return self._fail(held)
        if _on_edge_fraction(pts, cx, cy, theta, a, b) < MIN_ON_EDGE:
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

        if not self._locked:
            # Nothing to match against, so the frame is defined here: x along the longer wall
            # (section 4). Without this the room frame would come out differently depending on
            # which wall Scout happened to be facing when it started.
            px, py, heading, w, l = self._read_as(cx, cy, theta, a, b, 0 if a >= b else 1)
            self.room = (round(w), round(l))
            self._locked = True
            log.info("room frame locked: %d x %d mm", *self.room)
            self._warn_if_square()
        else:
            ceiling = min(MAX_JUMP_COST + LOST_BUDGET_PER_SCAN * self._lost, MAX_LOST_BUDGET)
            best = None
            for k in range(4):
                px, py, heading, w, l = self._read_as(cx, cy, theta, a, b, k)
                # This rectangle is supposed to be the room, and the room's size is already known.
                # When it is not, the scan did not see the whole room -- an obstacle is occluding a
                # wall -- and the corner it measured from is the wrong corner, which lands the pose
                # a metre out while still looking self-consistent. Refuse it.
                if abs(w - self.room[0]) + abs(l - self.room[1]) > SIZE_REJECT_MM:
                    continue
                dh = abs((heading - self.heading + 180) % 360 - 180)
                # Match the lock on continuity. Position carries most of the weight: between scans
                # Scout can move about 80 mm, so a wrong reading throws the position metres away
                # and is never the cheapest. Heading alone cannot tell the four apart mid-corner.
                cost = dh + math.hypot(px - self.x, py - self.y) / JUMP_PER_DEG_MM
                # The budget grows with every scan since the last accepted pose: after a second of
                # no fits Scout really has moved, so the honest jump is bigger than it is between
                # two consecutive scans. It grows by what Scout could have driven, not without
                # bound -- at 10 Hz and cruise that is about 40 mm a scan, and the allowance here
                # is deliberately twice that so a slow fix never costs the lock.
                if cost > ceiling:
                    continue
                if best is None or cost < best[0]:
                    best = (cost, px, py, heading, w, l)

            if best is not None:
                _, px, py, heading, w, l = best
            else:
                # Nothing matches the lock. Scout was picked up, a wall is hidden, or this is not
                # the same room, so report no pose rather than a confident wrong one.
                self._lost += 1
                if self._lost < RELOCK_AFTER:
                    return self._fail(held)
                # Give up and re-acquire, defining the frame afresh exactly as a first lock does.
                # Re-acquiring matters as much as refusing: if no rotation ever matches again --
                # Scout was carried to another room, or turned far enough while lost that the jump
                # stays unaffordable -- then without this it would stay blind for the rest of the
                # run. The new frame has no relation to the old one, so every point already on the
                # map is now in the wrong frame: say so and let the server throw the map away
                # rather than silently re-basing it.
                px, py, heading, w, l = self._read_as(cx, cy, theta, a, b, 0 if a >= b else 1)
                log.warning("pose lost for %d scans: re-acquiring the room frame, the map is void",
                            self._lost)
                self.relocked = True
                self.room = (round(w), round(l))
                self._warn_if_square()
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
