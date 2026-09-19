"""RPLIDAR reader thread: keeps the latest full 360-entry scan and the openings found in it
(PROTOCOL.md section 6). Reconnects forever.

The gap finder is vendored in `gaps.py`. It matters here because it is the only thing that can tell
an opening from a surface that does not reflect: both come back as nothing at all. Every gap carries
that distinction as `evidence`, and the audit refuses to measure anything but `see_through`."""
import logging
import threading
import time
from typing import Optional

import serial

from . import gaps as gapfinder
from . import ports, rplidar

log = logging.getLogger("scout.lidar")

GAP_EVERY = 3                   # find gaps every Nth rotation: about 2 Hz, and it is the costly part
MIN_GAP_MM = 150.0              # below this is sensor noise rather than an opening

def scan_from_rotation(rotation, offset_deg=0.0):
    """rotation: [(lidar_angle_deg, dist_mm), ...] for one turn, RPLIDAR convention (clockwise,
    0 = its front). Returns a list of exactly 360 integers, index i = millimetres at bearing i
    degrees counter-clockwise of straight ahead, 0 for no return (PROTOCOL.md section 6).

    Each bin keeps the second-smallest return that fell in it, so a single stray short sample --
    dust, a reflection off a chair leg -- cannot invent an obstacle. Bins with one sample keep it."""
    bins = [[] for _ in range(360)]
    for ang, d in rotation:
        if d <= 0:
            continue
        # RPLIDAR angles grow clockwise and ours grow to the left, so the sign flips
        i = int(round((offset_deg - ang) % 360.0)) % 360
        bins[i].append(d)
    out = [0] * 360
    for i, ds in enumerate(bins):
        if not ds:
            continue
        ds.sort()
        out[i] = int(round(ds[1] if len(ds) > 1 else ds[0]))
    return out


def gap_to_wire(g):
    """One gap as the protocol carries it (section 6). Field names match v1.2's scan frame, so
    anything already drawing gaps keeps working."""
    a = g.mid_angle_deg
    return {"a0": round(g.start_angle_deg, 1), "mm0": int(g.start_mm),
            "a1": round(g.end_angle_deg, 1), "mm1": int(g.end_mm),
            "width_mm": int(g.width_mm), "span_deg": round(g.span_deg, 1),
            "mid_deg": round(a - 360.0 if a > 180.0 else a, 1),
            "evidence": g.evidence}


class Lidar:
    def __init__(self, setting, offset_deg=0.0):
        self.enabled = setting != "none"
        self.fixed_port = setting if setting and setting != "none" else None   # from the environment
        self.port_hint = None      # from the startup probe, used once
        self.offset_deg = offset_deg
        self.port = None
        self.info = ""
        self.connected = False
        self.scan = []                  # latest 360-entry scan, or [] when absent
        self.gaps = []                  # openings in that scan, each with its evidence
        self.hz = 0.0                   # measured rotation rate
        self.scan_mode = "none"         # express or standard, whichever the board negotiated
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
                    self.scan = []
                    self.gaps = []
                    self.gaps = []
                    ports.in_use.discard(device)
                    log.error("LIDAR DISCONNECTED: width off until it is back")
            else:
                self._misses += 1
                if self._misses == 1 or self._misses % 20 == 0:
                    log.error("LIDAR NOT FOUND: width disabled. USB serial ports seen: %s. "
                              "Set SCOUT_LIDAR_PORT=/dev/... to force one.", ports.seen())
            time.sleep(3)

    def _session(self, device):
        lidar = rplidar.RPLidar(device, timeout_s=1.0)
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
                scan = scan_from_rotation(rotation, self.offset_deg)
                self.scan = scan
                now = time.monotonic()
                dt, last = now - last, now
                if 0.02 < dt < 2.0:
                    self.hz = round(1.0 / dt, 1) if self.hz == 0 else round(0.7 * self.hz + 0.3 / dt, 1)
                self.scan_mode = lidar.scan_mode
                n += 1
                if n % GAP_EVERY == 0:
                    # the finder wants (bearing, mm) pairs; the scan is already in Scout's frame
                    pts = [(float(i), float(mm)) for i, mm in enumerate(scan)]
                    self.gaps = [gap_to_wire(g) for g in gapfinder.find_gaps(pts, MIN_GAP_MM)]
        finally:
            try:
                lidar.close()
            except Exception:
                pass
