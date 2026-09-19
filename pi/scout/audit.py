"""Slope stop-and-measure and width pinch detection (PROTOCOL.md section 5). Pure logic, no I/O."""


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

    def step(self, now, pitch, width_mm):
        """now in seconds. pitch in degrees or None when there is no IMU. width_mm 0 when invalid.
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

        # ---- width: one event per pinch point ----
        limit = float(self.cfg["width_limit_mm"]) * float(self.cfg["scale"])
        threshold = 1.4 * limit
        if self._pinch is None:
            if 0 < width_mm < threshold:
                self._pinch = {"min": width_mm, "t0": now}
        else:
            if width_mm > 0:
                self._pinch["min"] = min(self._pinch["min"], width_mm)
            if width_mm >= threshold or now - self._pinch["t0"] >= 3.0:
                value = self._pinch["min"]
                events.append({"kind": "width_fail" if value < limit else "width_pass",
                               "value": value, "unit": "mm", "limit": round(limit, 1), "scale": float(self.cfg["scale"])})
                self._pinch = None

        return events, stop
