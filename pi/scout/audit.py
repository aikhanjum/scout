"""Slope stop-and-measure and width pinch detection (PROTOCOL.md section 5). Pure logic, no I/O."""

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


class Audit:
    def __init__(self, cfg):
        self.cfg = cfg                  # the live runtime config dict, shared with the server
        self.measuring = False
        self._slope = "armed"           # armed -> settle -> average -> cooldown -> armed
        self._t_above = None
        self._t_stage = 0.0
        self._sum = 0.0
        self._n = 0
        self._t_below = None
        self._pinch = None              # None, or {"min": mm, "t0": s} while a pinch point is open
        self._gap_mm = 0                # last qualifying gap width, held for GAP_LATCH_S
        self._gap_at = None             # when that gap was last actually seen

    def step(self, now, pitch, width_mm, gaps=None):
        """now in seconds. pitch in degrees or None when there is no IMU. width_mm 0 when invalid.
        gaps is the latest scan frame's gap list, or None when there is no lidar.
        Returns (events, stop_motors). Events lack t, seq and space; the server fills those."""
        events = []
        stop = False

        # ---- slope: one event per ramp ----
        if pitch is None:
            if self._slope != "armed":
                self._slope, self.measuring = "armed", False
        elif self._slope == "armed":
            if abs(pitch) > 2.0:
                self._t_above = self._t_above if self._t_above is not None else now
                if now - self._t_above >= 0.5:
                    self._slope, self._t_stage, self.measuring, stop = "settle", now, True, True
            else:
                self._t_above = None
        elif self._slope == "settle":
            if now - self._t_stage >= 0.4:
                self._slope, self._t_stage, self._sum, self._n = "average", now, 0.0, 0
        elif self._slope == "average":
            self._sum += pitch
            self._n += 1
            if now - self._t_stage >= 1.0:
                value = round(abs(self._sum / max(self._n, 1)), 1)
                limit = float(self.cfg["slope_limit_deg"])
                events.append({"kind": "slope_fail" if value > limit else "slope_pass",
                               "value": value, "unit": "deg", "limit": limit, "scale": 1.0})
                self._slope, self.measuring, self._t_below, self._t_above = "cooldown", False, None, None
        elif self._slope == "cooldown":
            if abs(pitch) < 1.0:
                self._t_below = self._t_below if self._t_below is not None else now
                if now - self._t_below >= 1.0:
                    self._slope = "armed"
            else:
                self._t_below = None

        # ---- width: one event per pinch point, from two sources ----
        # width_mm is the corridor at Scout's own position. A see_through gap is a
        # doorway it can see ahead. Either can open the pinch and both feed the
        # minimum, so a gate that is seen and then driven through is one event.
        limit = float(self.cfg["width_limit_mm"]) * float(self.cfg["scale"])
        threshold = 1.4 * limit
        # Scans arrive at 2 Hz and a gap's evidence can change between rotations, so a
        # gap is held briefly after it was last seen. Without that, one flickering
        # doorway closes and reopens the pinch and fires an event every couple of
        # seconds. A gap that really goes away still clears within GAP_LATCH_S.
        seen = audit_width_from_gaps(gaps, threshold)
        if seen:
            self._gap_mm, self._gap_at = seen, now
        elif self._gap_at is not None and now - self._gap_at >= GAP_LATCH_S:
            self._gap_mm, self._gap_at = 0, None
        gap_mm = self._gap_mm
        narrow = [w for w in (width_mm, gap_mm) if 0 < w < threshold]
        if self._pinch is None:
            if narrow:
                self._pinch = {"min": min(narrow), "t0": now}
        else:
            if narrow:
                self._pinch["min"] = min(self._pinch["min"], *narrow)
            if not narrow or now - self._pinch["t0"] >= PINCH_MAX_S:
                value = self._pinch["min"]
                events.append({"kind": "width_fail" if value < limit else "width_pass",
                               "value": value, "unit": "mm", "limit": round(limit, 1), "scale": float(self.cfg["scale"])})
                self._pinch = None

        return events, stop
