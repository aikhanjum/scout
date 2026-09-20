"""The 2D map: an occupancy grid in the room frame, and the obstacle clusters pulled out of it
(PROTOCOL.md section 6).

Cells hold a small signed score rather than a flag, so one stray return cannot paint a phantom
obstacle and one missed return cannot rub out a real one. A cell has to be hit repeatedly to read
as occupied.

Obstacles are whatever is occupied and is not the room's own walls. Scout reports each one once:
`claim_near` hands a cluster out the first time Scout has been close to it and never again, so the
server never emits two events for the same chair.
"""
import math

CELL_MM = 50
MARGIN_MM = 300          # grid padding around the room, so a wall never lands on the last cell
OCC_HIT = 2              # score added to the cell a return lands in
OCC_MISS = 1             # score taken off every cell the beam passed through
OCC_MAX = 8              # score clamp, so a cell can still change its mind
OCC_THRESHOLD = 2        # at or above this the cell is drawn occupied
FREE_THRESHOLD = -2      # at or below this the cell is drawn free
RAY_STEP_DEG = 2         # every other bearing is plenty at 50 mm cells and costs half the CPU

WALL_TOL_MM = 250        # occupied cells this close to a fitted wall are the wall, not an obstacle
MIN_CLUSTER_CELLS = 3    # smaller than this is noise (3 cells at 50 mm is about 80 mm across)
MERGE_MM = 500           # a cluster this close to one already reported is the same object
MAX_OBJECT_MM = 1200     # with no fitted room, a run of cells longer than this is a wall, not a thing
CLAIM_MM = 1200          # how near Scout must have come for a cluster to be worth reporting
SETTLE_SCANS = 10        # scans a cluster must stop growing for before it is claimed (1 s at 10 Hz)


class Grid:
    def __init__(self):
        self.clear()

    def clear(self, room=None, span_mm=0):
        self.room = room
        self.cell = CELL_MM
        if room:
            self.w = int(room[0] / CELL_MM) + 2 * int(MARGIN_MM / CELL_MM)
            self.h = int(room[1] / CELL_MM) + 2 * int(MARGIN_MM / CELL_MM)
            self.ox = self.oy = -MARGIN_MM               # room-frame mm of cell (0,0)'s centre
        elif span_mm:
            # Slam fits no rectangle, so there is no room to size the grid from. Lay out a fixed
            # canvas with Scout's starting point at its centre instead. frame() crops to the part
            # that has actually been seen, so the rest of the canvas costs nothing on the wire.
            n = int(span_mm / CELL_MM)
            self.w = self.h = n
            self.ox = self.oy = -(n // 2) * CELL_MM
        else:
            self.w = self.h = 1
            self.ox = self.oy = 0
        self.score = bytearray(self.w * self.h)          # stored +OCC_MAX biased, so 0..2*OCC_MAX
        for i in range(len(self.score)):
            self.score[i] = OCC_MAX
        self._reported = []                              # (x_mm, y_mm) of obstacles already emitted
        self._seen = None                                # [x0, y0, x1, y1] cells ever touched
        self._pending = {}                               # (x_mm, y_mm) -> (cells, scans_steady)

    def ready(self):
        """True once there is a canvas to draw on -- from a fitted room, or from slam's span."""
        return self.w > 1

    def _cell(self, x, y):
        return int(round((x - self.ox) / self.cell)), int(round((y - self.oy) / self.cell))

    def _bump(self, cx, cy, delta):
        if 0 <= cx < self.w and 0 <= cy < self.h:
            i = cy * self.w + cx
            self.score[i] = max(0, min(2 * OCC_MAX, self.score[i] + delta))
            b = self._seen
            if b is None:
                self._seen = [cx, cy, cx, cy]
            else:
                if cx < b[0]: b[0] = cx
                if cy < b[1]: b[1] = cy
                if cx > b[2]: b[2] = cx
                if cy > b[3]: b[3] = cy

    def integrate(self, scan, px, py, heading_deg):
        """Fold one scan, taken at a known pose, into the grid."""
        if not self.ready():
            return
        for i in range(0, 360, RAY_STEP_DEG):
            mm = scan[i]
            if mm <= 0:
                continue
            a = math.radians(heading_deg + i)
            dx, dy = math.cos(a), math.sin(a)
            # everything the beam passed through is free
            d = 0
            while d < mm - self.cell:
                self._bump(*self._cell(px + dx * d, py + dy * d), -OCC_MISS)
                d += self.cell
            # where it stopped is occupied
            self._bump(*self._cell(px + dx * mm, py + dy * mm), OCC_HIT)

    def _window(self):
        """The part of the canvas worth sending: (x0, y0, w, h) in cells.

        A room-sized grid is sent whole, exactly as before. Slam's canvas is mostly empty -- it is
        sized for the largest space Scout might walk, not the one it is in -- so send the rectangle
        that has actually been touched. A 12 m canvas is 57 kB of cells a second on a phone
        hotspot; one room's worth of it is a fifth of that."""
        if self.room or self._seen is None:
            return 0, 0, self.w, self.h
        x0, y0, x1, y1 = self._seen
        x0, y0 = max(0, x0 - 1), max(0, y0 - 1)
        x1, y1 = min(self.w - 1, x1 + 1), min(self.h - 1, y1 + 1)
        return x0, y0, x1 - x0 + 1, y1 - y0 + 1

    def cells_string(self, x0=0, y0=0, w=None, h=None):
        """'0' unknown, '1' free, '2' occupied, row-major (PROTOCOL.md section 6)."""
        w = self.w if w is None else w
        h = self.h if h is None else h
        out = bytearray(w * h)
        k = 0
        for gy in range(y0, y0 + h):
            base = gy * self.w
            for gx in range(x0, x0 + w):
                v = self.score[base + gx] - OCC_MAX
                out[k] = 0x32 if v >= OCC_THRESHOLD else 0x31 if v <= FREE_THRESHOLD else 0x30
                k += 1
        return out.decode("ascii")

    def frame(self, t, pose_ok):
        x0, y0, w, h = self._window()
        return {"type": "map", "t": t, "cell_mm": self.cell, "w": w, "h": h,
                "origin": [round(self.ox + x0 * self.cell), round(self.oy + y0 * self.cell)],
                "cells": self.cells_string(x0, y0, w, h), "pose": bool(pose_ok)}

    # ---- obstacles ----
    def _is_wall(self, x, y):
        """True when this point sits on the room's own boundary rather than on something in it."""
        w, l = self.room
        return min(abs(x), abs(x - w), abs(y), abs(y - l)) <= WALL_TOL_MM

    def _blobs(self):
        """Connected runs of occupied non-wall cells, each as a list of (cx, cy)."""
        if not self.ready():
            return []
        occ = set()
        for i, s in enumerate(self.score):
            if s - OCC_MAX >= OCC_THRESHOLD:
                cx, cy = i % self.w, i // self.w
                x, y = self.ox + cx * self.cell, self.oy + cy * self.cell
                if self.room and self._is_wall(x, y):
                    continue
                occ.add((cx, cy))
        out = []
        while occ:
            seed = occ.pop()
            blob, stack = [seed], [seed]
            while stack:
                cx, cy = stack.pop()
                for n in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1),
                          (cx + 1, cy + 1), (cx - 1, cy - 1), (cx + 1, cy - 1), (cx - 1, cy + 1)):
                    if n in occ:
                        occ.discard(n)
                        blob.append(n)
                        stack.append(n)
            if len(blob) < MIN_CLUSTER_CELLS:
                continue
            if not self.room:
                # With no fitted rectangle there is no "the walls are the edges" rule to lean on,
                # so tell a wall from an object by how far it runs: anything longer than any piece
                # of furniture is the building. Without this, every wall slam maps is handed out
                # as an obstacle.
                xs = [c[0] for c in blob]
                ys = [c[1] for c in blob]
                if max(max(xs) - min(xs), max(ys) - min(ys)) * self.cell > MAX_OBJECT_MM:
                    continue
            out.append(blob)
        return out

    def _centre(self, blob):
        mx = sum(c[0] for c in blob) / len(blob)
        my = sum(c[1] for c in blob) / len(blob)
        return round(self.ox + mx * self.cell), round(self.oy + my * self.cell)

    def _nearest_mm(self, blob, px, py):
        """Distance to the closest cell of a cluster."""
        return min(math.hypot(self.ox + cx * self.cell - px, self.oy + cy * self.cell - py)
                   for cx, cy in blob)

    def clusters(self):
        """Connected runs of occupied non-wall cells. Returns [(x_mm, y_mm, cells), ...]."""
        return [(*self._centre(b), len(b)) for b in self._blobs()]

    def claim_near(self, px, py, radius_mm=CLAIM_MM):
        """Every cluster Scout has come close to, claimed once each.

        The lidar sees all the way round, so what decides whether an obstacle can be reported is
        how near Scout has come to it, not whether it is facing it. A bin passed at arm's length is
        scanned from every side and placed to the centimetre, and one straight ahead is no more of
        an obstacle than one alongside.

        Two gates decide when. The cluster must come within `radius_mm`, so a four-cell smudge
        several metres off is left alone until Scout has been close to it. And it must have
        stopped growing for SETTLE_SCANS scans, because the centroid of a half-seen object sits on
        its near edge and an event cannot be corrected once it is on the wire.

        Range is to the nearest cell of the cluster, not its centroid: the centroid walks outward
        as the far side fills in, and a thing whose centre is 1.3 m off can have its near face at
        0.9 m.

        Returns [(x_mm, y_mm, cells), ...], usually empty.
        """
        out, pending = [], {}
        for blob in self._blobs():
            if self._nearest_mm(blob, px, py) > radius_mm:
                continue
            x, y = self._centre(blob)
            n = len(blob)
            if any(math.hypot(x - rx, y - ry) <= MERGE_MM for rx, ry in self._reported):
                continue
            # the centroid shifts as the far side fills in, so a candidate is matched by distance
            cells, steady, best = n, 0, None
            for (kx, ky), v in self._pending.items():
                gap = math.hypot(x - kx, y - ky)
                if gap <= MERGE_MM and (best is None or gap < best[0]):
                    best = (gap, v)
            if best is not None:
                kc, ks = best[1]
                cells = max(n, kc)
                steady = ks + 1 if n <= kc else 0
            if steady >= SETTLE_SCANS:
                self._reported.append((x, y))
                out.append((x, y, n))
            else:
                pending[(x, y)] = (cells, steady)
        self._pending = pending      # a cluster that went out of range or merged drops its wait
        return out

    def nearest_reported(self, x, y):
        """The reported obstacle closest to a point, or None. Used to say whether a pinch is
        against a wall or against something standing in the room."""
        if not self._reported:
            return None
        return min(self._reported, key=lambda p: math.hypot(p[0] - x, p[1] - y))
