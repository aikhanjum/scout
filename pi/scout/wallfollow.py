"""Roomba-style wall following, from the lidar alone (PROTOCOL.md section 6, behaviour 4).

Scout holds a fixed distance from the wall on its right and drives forward. When something blocks
the way it stops, then turns away to go around. No pose is involved: the behaviour is a reflex on
the current scan. Pose only decides whether what it finds can be written onto the map, so Scout
keeps working in a room it cannot localise in.

Nothing here blocks or sleeps. `step` is called once per scan and returns the next (v, w).
"""
import math

FRONT_DEG = 25           # half-width of the cone Scout calls "ahead"
SIDE_DEG = 20            # half-width of the cone it measures the right-hand wall in
STOP_MM = 380            # something closer than this, dead ahead, stops Scout
CLEAR_MM = 520           # and it will not drive on until the way ahead is at least this clear
LOST_WALL_MM = 1200      # no wall on the right within this: assume an outside corner, curve right
KP = 0.0022              # turn per mm of wall-distance error
MAX_W = 0.55
TURN_W = 0.5             # turn rate when pivoting in place away from an obstacle
STUCK_S = 8.0            # pivoting for this long without escaping means back out instead


def _min_in(scan, centre_deg, half_deg):
    """Nearest return in a cone, or 0 when the cone is empty."""
    best = 0
    for d in range(int(centre_deg - half_deg), int(centre_deg + half_deg) + 1):
        mm = scan[d % 360]
        if mm > 0 and (best == 0 or mm < best):
            best = mm
    return best


class WallFollow:
    def __init__(self, cfg):
        self.cfg = cfg
        self.blocked = False      # true while Scout is held up by something in front of it
        self._since = None        # when it first became blocked
        self._backing_until = None

    def reset(self):
        self.blocked = False
        self._since = None
        self._backing_until = None

    def step(self, now, scan):
        """One scan in, (v, w) out. Also updates `blocked`, true while something in front is
        holding Scout up."""
        if not scan or len(scan) != 360:
            return 0.0, 0.0
        cruise = float(self.cfg["cruise"])
        target = float(self.cfg["wall_target_mm"])

        front = _min_in(scan, 0, FRONT_DEG)
        right = _min_in(scan, -90, SIDE_DEG)

        # backing out of a dead end: committed for a fixed stretch, so it cannot dither
        if self._backing_until is not None:
            if now < self._backing_until:
                return -cruise * 0.6, TURN_W * 0.5
            self._backing_until = None
            self._since = None

        if front and front < STOP_MM:
            if not self.blocked:
                self.blocked = True
                self._since = now
            if now - self._since > STUCK_S:
                self._backing_until = now + 1.5
            return 0.0, TURN_W          # pivot left, away from the wall on the right
        if self.blocked and (not front or front > CLEAR_MM):
            self.blocked = False        # the way opened up
        if self.blocked:
            return 0.0, TURN_W

        if not right or right > LOST_WALL_MM:
            return cruise * 0.7, -TURN_W * 0.6    # wall gone: curve right to find it again

        # hold the wall: positive error means too far from it, so turn right towards it
        w = max(-MAX_W, min(MAX_W, KP * (right - target)))
        return cruise, -w
