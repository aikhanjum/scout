"""RPLIDAR reader thread: keeps the latest five-angle sweep, and the latest whole
rotation with its gaps (PROTOCOL.md section 5, telem.sweep and the scan frame).
Reconnects forever."""
import logging
import threading
import time
from typing import Optional

import serial

from . import gaps as gapfinder
from . import ports, rplidar

log = logging.getLogger("scout.lidar")

ANGLES = (-90, -45, 0, 45, 90)   # ours: 0 ahead, negative right, positive left
WINDOW_DEG = 5.0
GAP_EVERY = 5                    # find gaps every Nth rotation: about 2 Hz, which is the scan frame rate
SCAN_MIN_GAP_MM = 150.0          # below the 1:4 course's 190 mm gate, so the demo gate shows up
SCAN_MAX_GAP_MM = 3000.0         # wider than any doorway: that is open space, not an opening


def _angdiff(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def sweep_from_rotation(rotation, offset_deg=0.0):
    """rotation: [(lidar_angle_deg, dist_mm), ...] for one turn, RPLIDAR convention (clockwise, 0 = its front).
    Returns ((a, mm), ...) for ANGLES: the second-smallest return within ±WINDOW_DEG of each bearing
    (one stray sample cannot fake a wall), 0 when there is no return."""
    out = []
    for a in ANGLES:
        target = (offset_deg - a) % 360.0          # RPLIDAR angles grow clockwise; ours grow to the left
        ds = sorted(d for ang, d in rotation if d > 0 and _angdiff(ang, target) <= WINDOW_DEG)
        mm = ds[1] if len(ds) > 1 else (ds[0] if ds else 0)
        out.append((a, int(round(mm))))
    return tuple(out)


def to_scout_angle(lidar_angle_deg, offset_deg=0.0):
    """Lidar bearing to Scout's convention: 0 ahead, negative right, in (-180, 180]."""
    a = (offset_deg - lidar_angle_deg) % 360.0
    return a - 360.0 if a > 180.0 else a


def unwrap(a):
    """[0, 360) back to Scout's (-180, 180]."""
    return round(a - 360.0 if a > 180.0 else a, 1)


def gaps_in_scout_frame(pts):
    """pts are already Scout angles. find_gaps works on a [0, 360) ring, so shift in and back."""
    found = gapfinder.find_gaps([(a % 360.0, mm) for a, mm in pts], min_gap_mm=SCAN_MIN_GAP_MM)
    return [{"a0": unwrap(g.start_angle_deg), "mm0": int(round(g.start_mm)),
             "a1": unwrap(g.end_angle_deg), "mm1": int(round(g.end_mm)),
             "width_mm": int(round(g.width_mm)), "span_deg": round(g.span_deg, 1),
             "evidence": g.evidence}
            for g in found if g.width_mm <= SCAN_MAX_GAP_MM]


def rotation_in_scout_frame(rotation, offset_deg=0.0):
    """One rotation as [(scout_angle_deg, mm), ...] sorted by angle, no-returns kept."""
    pts = [(round(to_scout_angle(a, offset_deg), 1), int(round(d))) for a, d in rotation]
    pts.sort(key=lambda p: p[0])
    return pts


class Lidar:
    def __init__(self, setting, offset_deg=0.0):
        self.enabled = setting != "none"
        self.fixed_port = setting if setting and setting != "none" else None   # from the environment
        self.port_hint = None      # from the startup probe, used once
        self.offset_deg = offset_deg
        self.port = None
        self.info = ""
        self.connected = False
        self.sweep = ()                 # latest ((a, mm), ...) or () when absent
        self.scan = None                # latest {"pts", "gaps", "hz", "mode"} or None
        self.scan_mode = "none"
        self.hz = 0.0
        self._misses = 0

    def start(self):
        if not self.enabled:
            log.warning("LIDAR DISABLED (SCOUT_LIDAR_PORT=none): width off")
            return
        threading.Thread(target=self._run, name="lidar", daemon=True).start()

    def _run(self):
        while True:
            device = self.fixed_port or self.port_hint
            self.port_hint = None
            if device is None:
                device, info = ports.find("lidar")
                if device:
                    self.info = f"model {info.model} fw {info.firmware} sn {info.serial[-6:]}"
            if device:
                self._misses = 0
                try:
                    self._session(device)
                except (rplidar.ProtocolError, rplidar.LidarNotFound, serial.SerialException, OSError) as e:
                    log.warning("LIDAR %s: %s", device, e)
                finally:
                    self.connected = False
                    self.sweep = ()
                    self.scan = None
                    ports.in_use.discard(device)
                    log.error("LIDAR DISCONNECTED: width off until it is back")
            else:
                self._misses += 1
                if self._misses == 1 or self._misses % 20 == 0:
                    log.error("LIDAR NOT FOUND: width disabled. USB serial ports seen: %s. "
                              "Set SCOUT_LIDAR_PORT=/dev/... to force one.", ports.seen())
            time.sleep(3)

    def _session(self, device):
        lidar = rplidar.RPLidarA1(device, timeout_s=1.0)
        try:
            ports.in_use.add(device)
            info = lidar.get_info()
            health, code = lidar.get_health()
            self.info = f"model {info.model} fw {info.firmware} sn {info.serial[-6:]} health {health}"
            if health == "ERROR":
                raise rplidar.ProtocolError(f"lidar reports internal error {code}; power-cycle it")
            self.port = device
            self.connected = True
            log.info("LIDAR connected on %s (%s)", device, self.info)
            n, last = 0, time.monotonic()
            for rotation in rplidar.rotations(lidar.nodes()):
                self.sweep = sweep_from_rotation(rotation, self.offset_deg)
                now = time.monotonic()
                dt = now - last
                last = now
                if 0.02 < dt < 2.0:
                    self.hz = round(1.0 / dt, 1) if self.hz == 0 else round(0.7 * self.hz + 0.3 / dt, 1)
                self.scan_mode = lidar.scan_mode
                n += 1
                if n % GAP_EVERY == 0:
                    pts = rotation_in_scout_frame(rotation, self.offset_deg)
                    self.scan = {"pts": pts, "hz": self.hz, "mode": lidar.scan_mode,
                                 "gaps": gaps_in_scout_frame(pts)}
        finally:
            try:
                lidar.close()
            except Exception:
                pass
