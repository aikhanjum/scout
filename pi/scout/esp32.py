"""The ESP32 bridge over USB serial (PROTOCOL.md section 9): JSON lines in, text commands out.
A thread owns the port and reconnects forever. The board drives the motors, keeps the 500 ms
watchdog, and beeps and blinks. It has no IMU: Scout senses with the lidar and the camera."""
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import serial

from . import ports

log = logging.getLogger("scout.esp32")
SILENCE_S = 3.0   # no line for this long means the board is gone


@dataclass(frozen=True)
class Drive:
    """What the board says it is doing, echoed back at 10 Hz."""
    v: float
    w: float


class Esp32:
    def __init__(self, setting):
        self.enabled = setting != "none"
        self.fixed_port = setting if setting and setting != "none" else None   # from the environment
        self.port_hint = None      # from the startup probe, used once
        self.port = None
        self.fw = ""
        self.connected = False
        self.drive: Optional[Drive] = None
        self._ser = None
        self._lock = threading.Lock()
        self._misses = 0

    def start(self):
        if not self.enabled:
            log.warning("ESP32 DISABLED (SCOUT_ESP32_PORT=none): driving off")
            return
        threading.Thread(target=self._run, name="esp32", daemon=True).start()

    def send(self, line):
        """Write one command line. Returns False when there is no board."""
        with self._lock:
            s = self._ser
            if s is None:
                return False
            try:
                s.write((line + "\n").encode())
                return True
            except (serial.SerialException, OSError) as e:
                log.warning("ESP32 write failed: %s", e)
                return False

    # ---- thread ----
    def _run(self):
        while True:
            device = self.fixed_port or self.port_hint
            self.port_hint = None
            if device is None:
                device, fw = ports.find("esp32")
                if device:
                    self.fw = fw
            if device:
                self._misses = 0
                try:
                    self._session(device)
                except (serial.SerialException, OSError, ValueError) as e:
                    log.warning("ESP32 %s: %s", device, e)
                finally:
                    with self._lock:
                        self._ser = None
                    self.connected = False
                    self.drive = None
                    ports.in_use.discard(device)
                    log.error("ESP32 DISCONNECTED: driving off until it is back")
            else:
                self._misses += 1
                if self._misses == 1 or self._misses % 20 == 0:
                    log.error("ESP32 NOT FOUND: driving disabled. USB serial ports seen: %s. "
                              "Set SCOUT_ESP32_PORT=/dev/... to force one.", ports.seen())
            time.sleep(3)

    def _session(self, device):
        with serial.Serial(device, 115200, timeout=0.5, exclusive=True) as s:
            ports.in_use.add(device)
            with self._lock:
                self._ser = s
            self.port = device
            self.connected = True
            log.info("ESP32 connected on %s (fw %s)", device, self.fw or "?")
            last = time.monotonic()
            while True:
                line = s.readline()
                if not line:
                    if time.monotonic() - last > SILENCE_S:
                        raise serial.SerialException(f"no data for {SILENCE_S:.0f} s")
                    continue
                last = time.monotonic()
                try:
                    obj = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if "v" in obj:
                    self.drive = Drive(float(obj.get("v", 0)), float(obj.get("w", 0)))
                elif "hello" in obj:
                    self.fw = str(obj.get("fw", "?"))
                    log.info("ESP32 hello: fw %s", self.fw)
                else:
                    log.info("ESP32: %s", line.decode("utf-8", "replace").strip())
