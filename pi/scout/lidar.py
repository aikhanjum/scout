"""RPLIDAR A1 reader thread: keeps the latest five-angle sweep (PROTOCOL.md section 5). Reconnects forever."""
import logging
import threading
import time
from typing import Optional

import serial

from . import ports, rplidar

log = logging.getLogger("scout.lidar")

ANGLES = (-90, -45, 0, 45, 90)   # ours: 0 ahead, negative right, positive left
WINDOW_DEG = 5.0


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
            for rotation in rplidar.rotations(lidar.nodes()):
                self.sweep = sweep_from_rotation(rotation, self.offset_deg)
        finally:
            try:
                lidar.close()
            except Exception:
                pass
