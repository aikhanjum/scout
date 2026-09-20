"""Where Scout is, by matching each scan against the map built so far (PROTOCOL.md section 4).

Scan-to-map matching, the architecture Hector SLAM uses. It is the right family for Scout because
Scout has no odometry and no IMU: there is no motion estimate to seed from, only the scans.

`pose.py` fits the room's rectangle in a single scan and reads position straight off it. Nothing
accumulates, so nothing drifts -- but there has to *be* a rectangle, so it works in one closed
rectangular room and nowhere else. This file trades that away. It keeps a map of everything seen so
far and asks where in that map this scan fits, which works in a corridor, an L, or two rooms
through a doorway.

HEADING comes from the walls when they are visible (wall_direction, the 4-theta Manhattan trick),
which took the real-room return-to-start error from 5.3 m to 2.9 m on 2026-09-20 -- and 2.9 m is still
a failure. Translation still slides on repetitive structure and half-empty scans. See docs/ACCURACY.md.

DRIFT IS REAL AND IT COMPOUNDS. Every match is a fraction of a cell out, and the next scan is
matched against a map that already carries that error. There is no loop closure here -- nor in
Hector -- so a long lap comes back to its start some centimetres from where it left. Measure it on
the day and quote that number. Do not quote pose.py's 2 mm: that figure belongs to a method this
one has replaced.

The matcher is brute force on purpose. For each candidate (dx, dy, dtheta) in a bounded window it
sums the map's likelihood under the scan's points and keeps the best -- coarse at 200 mm first,
then fine at 50 mm around that winner. No gradient descent to diverge, no line search to tune, and
when it misbehaves you can print the score surface and look at it. Rule 10.

Every gate fails closed, exactly as pose.py's do, because a confident wrong pose corrupts the map
for the rest of the run:

  - the match must clear MIN_SCORE, or this scan is not somewhere the map has been;
  - the winner must not sit on the edge of the search window. An edge winner means the real
    optimum lies outside the window and this is merely the best of a bad set;
  - the score surface must have a real peak in both directions. Slide a scan along a featureless
    corridor and the score does not change, because position along that corridor is genuinely not
    observable from a lidar -- no algorithm recovers it and a number there would be invention.
    The check is axis-aligned, so it catches a corridor lying along x or y and is weaker for one
    lying diagonally. Tightening it needs the eigenvectors of the score surface, not a bigger
    constant.

The map here is the matcher's own, kept apart from `mapping.Grid`. Both are built from the same
scans at the same poses, so they agree, but they want different things: the Grid wants crisp cells
to draw and to cluster, and the matcher wants a blurred field so that a nearly-right pose scores
nearly-right and there is a slope to climb.
"""
import logging
import math

import numpy as np

log = logging.getLogger("scout.slam")

CELL_MM = 50             # fine field resolution, same as the occupancy grid
COARSE = 2               # coarse field cells are this many fine cells across (100 mm)
SPAN_MM = 12000          # the canvas is this square, and Scout starts at the middle of it
K_R = 2                  # kernel radius in cells: a hit marks the 5x5 around it, fading outwards
K_SIGMA = 1.2            # in cells. Wider forgives more error and blurs the peak we are looking for

MAX_POINTS = 180         # the scan is subsampled to this before matching, for speed
MIN_POINTS = 60          # fewer returns than this is not a room

# Search windows. At 10 Hz a hand-carried lidar moves about 50 mm and turns about 9 degrees between
# scans; both windows are wider than that so that a dropped scan or two does not cost the lock.
C_HALF_MM, C_STEP_MM, C_HALF_DEG, C_STEP_DEG = 240.0, 80.0, 20.0, 2.5
# The fine window has to cover a whole coarse step either way, or a coarse answer that is right to
# within its own quantisation still lands outside the fine search and is thrown away as an edge hit.
F_HALF_MM, F_STEP_MM, F_HALF_DEG, F_STEP_DEG = 140.0, 20.0, 3.0, 0.5

# Ties are the common case, not the exception: shift a scan by less than a cell and nothing in the
# field changes, so a whole plateau of candidate poses scores identically. numpy's argmax then takes
# the lowest index, which is the *corner* of the search window -- the largest movement on offer. So
# break ties towards the prior instead. With no odometry, "Scout barely moved" is the only prior
# there is, and it is a good one at 10 Hz. Small enough to separate equals and never to outrank a
# real difference in score.
TIE_MM, TIE_DEG = 0.002, 0.05

# Heading from the walls themselves. Indoor walls are orthogonal, so one scan alone says which way
# the building's axes run, modulo 90 degrees; continuity between scans picks which of the four.
# With heading pinned this way the matcher only has to find x and y, and the ties that dragged it
# 60 to 150 degrees off in real rooms are gone. SCOUT_SLAM_MANHATTAN=0 turns it off.
import os as _os
MANHATTAN = _os.environ.get("SCOUT_SLAM_MANHATTAN", "1") != "0"
MANHATTAN_MIN_CONF = 0.35  # below this the scan has no clear wall direction (a crowd, a curved room)
M_HALF_DEG, M_STEP_DEG = 4.0, 1.0      # rotation window around the wall-derived heading, coarse
MF_HALF_DEG, MF_STEP_DEG = 1.5, 0.5    # and fine

MIN_SCORE = 0.30         # fraction of the best possible score, below which this is not a match
FLAT_FRAC = 0.01         # the score must fall at least this much 50 mm either side of the peak
KEY_MM, KEY_DEG = 80.0, 4.0   # integrate a scan only after this much movement, so a parked robot
                              # does not burn the same rotation into the map ten times a second
HOLD_SCANS = 40          # scans a stopped robot may coast on its last pose (about 4 s at 10 Hz)
RELOCK_AFTER = 30        # consecutive failed matches before throwing the map away and restarting


def _kernel():
    """The stamp a single return leaves: full strength at the cell it landed in, fading outwards.

    Without the fade the score surface is a field of spikes and a pose 30 mm out scores zero, so
    there is nothing for the search to climb. The fade is what makes "nearly right" score nearly
    right."""
    k = np.zeros((2 * K_R + 1, 2 * K_R + 1), dtype=np.uint8)
    for dy in range(-K_R, K_R + 1):
        for dx in range(-K_R, K_R + 1):
            d2 = dx * dx + dy * dy
            k[dy + K_R, dx + K_R] = int(round(255 * math.exp(-d2 / (2 * K_SIGMA ** 2))))
    return k


KERNEL = _kernel()


def scan_xy(scan, stride=2):
    """The scan as (x, y) arrays in the robot frame. 0 means no return and is dropped.

    Bearings run counter-clockwise from straight ahead, the same convention as pose.scan_points and
    PROTOCOL.md section 6. Every other bearing is plenty: doubling the points buys a fraction of a
    cell of accuracy and costs twice the search."""
    a = np.arange(0, 360, stride, dtype=np.float64)
    d = np.asarray(scan, dtype=np.float64)[::stride]
    keep = d > 0
    d, a = d[keep], np.radians(a[keep])
    return d * np.cos(a), d * np.sin(a)


def wall_direction(scan):
    """The building's axis as seen from this scan, in degrees in [0, 90), and a confidence 0..1.

    Neighbouring returns that sit close together lie on one surface; the direction of each such
    little segment, folded onto 90 degrees with the 4-theta trick, votes with its length. A room
    with straight walls gives a sharp answer; a crowd or a round room gives a weak one."""
    d = np.asarray(scan, dtype=np.float64)
    idx = np.nonzero(d > 0)[0]
    if idx.size < 20:
        return 0.0, 0.0
    a = np.radians(idx)
    x, y = d[idx] * np.cos(a), d[idx] * np.sin(a)
    gap = np.diff(idx)
    dx, dy = np.diff(x), np.diff(y)
    seg = np.hypot(dx, dy)
    near = np.minimum(d[idx][:-1], d[idx][1:])
    ok = (gap <= 2) & (seg <= np.maximum(120.0, 0.10 * near)) & (near > 250)
    if ok.sum() < 10:
        return 0.0, 0.0
    theta = np.arctan2(dy[ok], dx[ok])
    w = seg[ok]
    z = np.sum(w * np.exp(4j * theta))
    conf = float(abs(z) / w.sum())
    return float(np.degrees(np.angle(z)) / 4.0) % 90.0, conf


def _search(arr, cell, ox, oy, x0, y0, th0, px, py, half_mm, step_mm, half_deg, step_deg):
    """Best pose in a window around (x0, y0, th0), by brute force.

    Returns (score_fraction, x, y, heading_deg, on_edge, surface, ti, tj) where `surface` is the
    (T, T) grid of translation scores at the winning rotation -- kept so the caller can ask whether
    the peak is a peak, rather than a plateau it happened to land on.
    """
    H, W = arr.shape
    flat = arr.ravel()
    n_t = int(half_mm / step_mm)
    offs = np.arange(-n_t, n_t + 1) * float(step_mm)
    tx, ty = np.meshgrid(offs, offs)
    tx, ty = tx.ravel(), ty.ravel()          # index m = row * T + col; col picks tx, row picks ty
    n_r = int(half_deg / step_deg)
    rots = np.arange(-n_r, n_r + 1) * float(step_deg)
    n = px.size
    near = TIE_MM * (np.abs(tx) + np.abs(ty))       # the tie-break, in score points
    best = None
    for ri, dth in enumerate(rots):
        h = math.radians(th0 + dth)
        c, s = math.cos(h), math.sin(h)
        rx, ry = px * c - py * s, px * s + py * c
        wx = (x0 + tx)[:, None] + rx[None, :]
        wy = (y0 + ty)[:, None] + ry[None, :]
        ix = np.rint((wx - ox) / cell).astype(np.int32)
        iy = np.rint((wy - oy) / cell).astype(np.int32)
        inside = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        np.clip(ix, 0, W - 1, out=ix)
        np.clip(iy, 0, H - 1, out=iy)
        raw = np.where(inside, flat[iy * W + ix], 0).sum(axis=1, dtype=np.int64)
        ranked = raw - near - TIE_DEG * abs(dth)
        m = int(np.argmax(ranked))
        if best is None or ranked[m] > best[0]:
            best = (float(ranked[m]), ri, m, raw)
    _, ri, m, sc = best
    total = int(sc[m])
    t = 2 * n_t + 1
    tj, ti = m % t, m // t
    on_edge = ri in (0, 2 * n_r) or tj in (0, t - 1) or ti in (0, t - 1)
    return (total / (n * 255.0), x0 + tx[m], y0 + ty[m], th0 + rots[ri],
            on_edge, sc.reshape(t, t), ti, tj)


class Slam:
    """Pose by scan matching. Same interface as pose.Pose, so the server does not know which it has."""

    def __init__(self, span_mm=SPAN_MM):
        self._span = span_mm
        self.clear()

    def clear(self):
        n = int(round(self._span / CELL_MM))
        n += (-n) % COARSE                         # a whole number of coarse cells
        self.w = self.h = n
        self.ox = self.oy = -(n // 2) * CELL_MM    # world mm of cell (0, 0)'s centre
        self.fine = np.zeros((n, n), dtype=np.uint8)
        self.coarse = np.zeros((n // COARSE, n // COARSE), dtype=np.uint8)
        self.ok = False
        self.x = self.y = self.heading = 0.0
        self.room = None          # there is no fitted rectangle here, and the protocol allows null
        self.score = 0.0          # the last match's quality, 0..1. Worth putting on a screen.
        self.relocked = False
        self._started = False
        self._lost = 0
        self._held = 0
        self._kx = self._ky = self._kh = 0.0       # pose at the last scan folded into the map
        self._wall = None         # the building's axis in the frame, set from the first scan
        self._mh = 0.0            # heading resolved from the walls, tracked through blackouts too

    # ---- the map the matcher searches ----
    def _stamp(self, x, y, th, px, py):
        """Fold one scan, taken at a known pose, into the likelihood field."""
        c, s = math.cos(math.radians(th)), math.sin(math.radians(th))
        wx, wy = x + px * c - py * s, y + px * s + py * c
        ix = np.rint((wx - self.ox) / CELL_MM).astype(np.int32)
        iy = np.rint((wy - self.oy) / CELL_MM).astype(np.int32)
        keep = (ix >= K_R) & (ix < self.w - K_R) & (iy >= K_R) & (iy < self.h - K_R)
        for cx, cy in zip(ix[keep].tolist(), iy[keep].tolist()):
            view = self.fine[cy - K_R:cy + K_R + 1, cx - K_R:cx + K_R + 1]
            np.maximum(view, KERNEL, out=view)
        # The coarse field is the fine one seen from further away: a coarse cell is lit if anything
        # in its block is. Rebuilding beats stamping twice -- one reshape over 60k bytes.
        n = self.h // COARSE
        self.coarse = self.fine.reshape(n, COARSE, n, COARSE).max(axis=(1, 3))
        self._kx, self._ky, self._kh = x, y, th

    def _moved_enough(self, x, y, th):
        return (math.hypot(x - self._kx, y - self._ky) >= KEY_MM
                or abs((th - self._kh + 180) % 360 - 180) >= KEY_DEG)

    # ---- the same failure handling pose.py has ----
    @property
    def _hold(self):
        if not (self._started and self.ok and self._held < HOLD_SCANS):
            return None
        return (self.x, self.y, self.heading)

    def _fail(self, held):
        if held is None:
            self._held = 0
            self.ok = False
            return False
        self.x, self.y, self.heading = held
        self._held += 1
        self.ok = True
        return True

    def update(self, scan, moving=True):
        """Match this scan into the map and move the pose. True when the pose is valid."""
        held = self._hold if not moving else None
        if not scan or len(scan) != 360:
            return self._fail(held)
        px, py = scan_xy(scan)
        if px.size < MIN_POINTS:
            return self._fail(held)
        if px.size > MAX_POINTS:
            step = int(math.ceil(px.size / MAX_POINTS))
            px, py = px[::step], py[::step]

        if not self._started:
            # Nothing to match against, so this scan *defines* the frame: Scout is at the origin
            # facing along x. Every later pose is relative to wherever Scout happened to start.
            self._started = True
            self.x = self.y = self.heading = 0.0
            self._stamp(0.0, 0.0, 0.0, px, py)
            self.ok, self.score, self._held = True, 1.0, 0
            self._wall, self._mh = wall_direction(scan)[0], 0.0
            log.info("slam: frame started, %d x %d mm canvas at %d mm cells",
                     self.w * CELL_MM, self.h * CELL_MM, CELL_MM)
            return True

        th_seed, c_half, c_step, f_half, f_step = self.heading, C_HALF_DEG, C_STEP_DEG, F_HALF_DEG, F_STEP_DEG
        if MANHATTAN and self._wall is not None:
            m, conf = wall_direction(scan)
            if conf >= MANHATTAN_MIN_CONF:
                # four headings put these walls on the building's axis; take the one nearest the last
                base = self._wall - m
                cands = [(base + k * 90.0 + 180.0) % 360.0 - 180.0 for k in range(4)]
                self._mh = min(cands, key=lambda h: abs((h - self._mh + 180.0) % 360.0 - 180.0))
                th_seed, c_half, c_step, f_half, f_step = self._mh, M_HALF_DEG, M_STEP_DEG, MF_HALF_DEG, MF_STEP_DEG
        cf, cx, cy, cth, _, _, _, _ = _search(
            self.coarse, CELL_MM * COARSE, self.ox, self.oy, self.x, self.y, th_seed,
            px, py, C_HALF_MM, C_STEP_MM, c_half, c_step)
        frac, x, y, th, on_edge, surf, ti, tj = _search(
            self.fine, CELL_MM, self.ox, self.oy, cx, cy, cth,
            px, py, F_HALF_MM, F_STEP_MM, f_half, f_step)

        why = None
        if frac < MIN_SCORE:
            why = "score %.2f under %.2f" % (frac, MIN_SCORE)
        elif on_edge:
            why = "the best pose is on the edge of the search window"
        elif not self._peaked(surf, ti, tj):
            why = "the score surface is flat: this position is not observable"
        if why:
            self._lost += 1
            if self._lost < RELOCK_AFTER:
                return self._fail(held)
            # Give up and start a new frame from this scan. The new frame has no relation to the
            # old one, so everything already on the map is now in the wrong place: say so and let
            # the server throw it away rather than silently re-basing it.
            log.warning("slam: no match for %d scans (%s): restarting the frame, the map is void",
                        self._lost, why)
            self.fine[:] = 0
            self._started = False
            self.relocked = True
            self._lost = 0
            return self.update(scan, moving)

        self._lost = 0
        self.x, self.y, self.heading = x, y, (th + 180) % 360 - 180
        self.ok, self.score, self._held = True, frac, 0
        if self._moved_enough(self.x, self.y, self.heading):
            self._stamp(self.x, self.y, self.heading, px, py)
        return True

    @staticmethod
    def _peaked(surf, ti, tj):
        """Does the score actually fall away either side of the winner, or is this a plateau?

        Two cells at the fine step is 50 mm. A real wall seen off by 50 mm loses a good deal of
        score; a corridor slid 50 mm along itself loses none, which is the case this rejects."""
        t = surf.shape[0]
        peak = float(surf[ti, tj])
        if peak <= 0:
            return False
        dx = peak - min(surf[ti, max(0, tj - 2)], surf[ti, min(t - 1, tj + 2)])
        dy = peak - min(surf[max(0, ti - 2), tj], surf[min(t - 1, ti + 2), tj])
        return dx / peak >= FLAT_FRAC and dy / peak >= FLAT_FRAC

    def take_relock(self):
        """True once after the frame was restarted. The caller must clear the map."""
        was, self.relocked = self.relocked, False
        return was

    def snapshot(self):
        return (self.ok, round(self.x), round(self.y), round(self.heading, 1), self.room)
