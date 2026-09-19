"""The 2D map: an occupancy grid in the room frame, and the obstacle clusters pulled out of it
(PROTOCOL.md section 6).

Cells hold a small signed score rather than a flag, so one stray return cannot paint a phantom
obstacle and one missed return cannot rub out a real one. A cell has to be hit repeatedly to read
as occupied.

Obstacles are whatever is occupied and is not the room's own walls. Scout reports each one once:
`new_obstacles` returns only clusters it has not handed out before, so the server never emits two
events for the same chair.
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


class Grid:
    def __init__(self):
        self.clear()

    def clear(self, room=None):
        self.room = room
        self.cell = CELL_MM
        if room:
            self.w = int(room[0] / CELL_MM) + 2 * int(MARGIN_MM / CELL_MM)
            self.h = int(room[1] / CELL_MM) + 2 * int(MARGIN_MM / CELL_MM)
        else:
            self.w = self.h = 1
        self.ox = self.oy = -MARGIN_MM if room else 0    # room-frame mm of cell (0,0)'s centre
        self.score = bytearray(self.w * self.h)          # stored +OCC_MAX biased, so 0..2*OCC_MAX
        for i in range(len(self.score)):
            self.score[i] = OCC_MAX
        self._reported = []                              # (x_mm, y_mm) of obstacles already emitted

    def ready(self):
        return self.room is not None

    def _cell(self, x, y):
        return int(round((x - self.ox) / self.cell)), int(round((y - self.oy) / self.cell))

    def _bump(self, cx, cy, delta):
        if 0 <= cx < self.w and 0 <= cy < self.h:
            i = cy * self.w + cx
            self.score[i] = max(0, min(2 * OCC_MAX, self.score[i] + delta))

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

    def cells_string(self):
        """'0' unknown, '1' free, '2' occupied, row-major (PROTOCOL.md section 6)."""
        out = bytearray(len(self.score))
        for i, s in enumerate(self.score):
            v = s - OCC_MAX
            out[i] = 0x32 if v >= OCC_THRESHOLD else 0x31 if v <= FREE_THRESHOLD else 0x30
        return out.decode("ascii")

    def frame(self, t, pose_ok):
        return {"type": "map", "t": t, "cell_mm": self.cell, "w": self.w, "h": self.h,
                "origin": [round(self.ox), round(self.oy)], "cells": self.cells_string(),
                "pose": bool(pose_ok)}

    # ---- obstacles ----
    def _is_wall(self, x, y):
        """True when this point sits on the room's own boundary rather than on something in it."""
        w, l = self.room
        return min(abs(x), abs(x - w), abs(y), abs(y - l)) <= WALL_TOL_MM

    def clusters(self):
        """Connected runs of occupied non-wall cells. Returns [(x_mm, y_mm, cells), ...]."""
        if not self.ready():
            return []
        occ = set()
        for i, s in enumerate(self.score):
            if s - OCC_MAX >= OCC_THRESHOLD:
                cx, cy = i % self.w, i // self.w
                x, y = self.ox + cx * self.cell, self.oy + cy * self.cell
                if not self._is_wall(x, y):
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
            if len(blob) >= MIN_CLUSTER_CELLS:
                mx = sum(c[0] for c in blob) / len(blob)
                my = sum(c[1] for c in blob) / len(blob)
                out.append((round(self.ox + mx * self.cell), round(self.oy + my * self.cell), len(blob)))
        return out

    def claim_ahead(self, px, py, heading_deg, reach_mm):
        """The obstacle Scout has stopped in front of, claimed so it is reported exactly once.

        Directional on purpose. Sweeping the whole grid instead would hand out every cluster the
        lidar can see from the doorway, while each one is still four cells and metres away, and
        then hand it out again once Scout is close enough to place it properly. Asking only about
        what is in front of the robot, at the moment it stops, is what section 6 promises.

        Returns (x_mm, y_mm, cells) or None.
        """
        a = math.radians(heading_deg)
        tx, ty = px + math.cos(a) * reach_mm, py + math.sin(a) * reach_mm
        near = [(math.hypot(x - tx, y - ty), x, y, n) for x, y, n in self.clusters()]
        near = [c for c in near if c[0] <= reach_mm]
        if not near:
            return None
        _, x, y, n = min(near)
        if any(math.hypot(x - rx, y - ry) <= MERGE_MM for rx, ry in self._reported):
            return None
        self._reported.append((x, y))
        return x, y, n

    def nearest_reported(self, x, y):
        """The reported obstacle closest to a point, or None. Used to say whether a pinch is
        against a wall or against something standing in the room."""
        if not self._reported:
            return None
        return min(self._reported, key=lambda p: math.hypot(p[0] - x, p[1] - y))
