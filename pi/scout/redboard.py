"""The motor board over USB serial (PROTOCOL.md section 9).

Scout's motor controller is a SparkFun RedBoard (ATmega328P, Uno clone, CH340 USB) carrying a
DK Electronics motor shield (an Adafruit v1 clone: 2x L293D behind an SN74HC595). It took the role
the ESP32 was meant to have; the ESP32 never arrived from the hardware desk.

Its firmware (`06_serial_drive.ino`) was working before the Pi side was adapted and is owned by the
firmware workstream, so **this file translates and the board does not**. `send()` still takes the
protocol's own lines and converts them; the board's replies are converted back into the same
`Drive` the rest of the service already reads. Nothing above this file knows which board is fitted,
which is why server.py did not change.

The board's dialect:

    "<L> <R>\\n"   drive, each -255..255, positive forward, skid steer, the pair on each side ganged
    "s\\n"         stop now
    "?\\n"         status
    -> "scoutable-motor-v1 ready"   banner, once, on every port open
    -> "ok <L> <R>"                 accepted, with the clamped values actually applied
    -> "err"                        not parseable
    -> "watchdog stop"              nothing received for 600 ms, the board stopped itself

Four differences from the ESP32 the protocol was written against, all absorbed here:

* **No buzzer and no LEDs.** `B` and `L` have nowhere to go and become no-ops, logged once each.
  A width verdict therefore reaches a human through the dashboard and its speech only -- one
  channel where the spec wanted three. This is a demo-visible loss, not a tidy internal detail.
* **The board only speaks when spoken to.** The ESP32 streamed at 10 Hz, so silence meant it had
  gone. This one answers a command and is otherwise quiet, so a `?` keep-alive does that job.
* **Opening the port resets the board** over DTR and it is deaf for about 1.5 s. A session waits
  for the banner before reporting itself connected; commands written before then are simply lost.
* **The firmware raises any non-zero duty below 70 to 70**, because the gearboxes hum without
  turning below that. A near-zero command would become a lurch, so `_duty` sends a real zero
  instead of letting a creep be inflated 9x.

The watchdog is the board's, not ours, and it is the only thing that stops the motors if this
process dies. It is 600 ms here where the ESP32's was 500 ms; the dashboard resends every 100 ms
while a key is held and wall following sends at 10 Hz, so both are satisfied by what already
exists. Never send anything on a timer that would pet it while Scout is moving.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import serial

from . import ports

log = logging.getLogger("scout.motor")

BANNER = ports.MOTOR_BANNER  # the boot banner; the port probe recognises the board by it too
SILENCE_S = 4.0              # no reply for this long means the board is gone
KEEPALIVE_S = 1.0            # ask "?" after this much of our own silence, so silence stays meaningful
BOOT_S = 2.5                 # DTR reset: deaf for about 1.5 s, so wait for the banner before writing
DEADBAND = 8                 # duty below this is sent as a real 0; see the note on the firmware floor


@dataclass(frozen=True)
class Drive:
    """What the board says it is doing, recovered from its `ok L R` acknowledgement."""
    v: float
    w: float


def _duty(x):
    """A side's -1..1 to the board's -255..255, with the deadband that keeps a creep from lurching."""
    d = max(-255, min(255, int(round(x * 255))))
    return 0 if abs(d) < DEADBAND else d


def _mix(v, w):
    """Protocol `v`/`w` (-1..1, w positive = left) to the board's left and right duty.

    Skid steer: for a left turn the left pair slows and the right pair speeds up. Saturation
    divides both sides by the overshoot rather than clipping one of them, so a turn commanded at
    full speed keeps its ratio instead of quietly straightening out.
    """
    l, r = v - w, v + w
    m = max(1.0, abs(l), abs(r))
    return _duty(l / m), _duty(r / m)


def _unmix(l, r):
    """The board's applied left and right duty back to `v`/`w`, so telemetry reports what it did."""
    return (l + r) / 2.0 / 255.0, (r - l) / 2.0 / 255.0


class RedBoard:
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
        self._last_write = 0.0
        self._misses = 0
        self._warned = set()

    def start(self):
        if not self.enabled:
            log.warning("MOTOR BOARD DISABLED (SCOUT_MOTOR_PORT=none): driving off")
            return
        threading.Thread(target=self._run, name="motor", daemon=True).start()

    # ---- the protocol's lines in, the board's dialect out ----
    def send(self, line):
        """Take one PROTOCOL.md section 9 line and write the board's equivalent.

        Returns False when there is no board or the line has no equivalent. `B`, `L` and `T`
        return True: the board has no buzzer, no lamps and no self-test, and a caller asking for
        one has not failed at anything it could have done differently.
        """
        parts = line.split()
        if not parts:
            return False
        k = parts[0]
        if k == "D" and len(parts) == 3:
            try:
                l, r = _mix(float(parts[1]), float(parts[2]))
            except ValueError:
                return False
            return self._write(f"{l} {r}")
        if k == "S":
            return self._write("s")
        if k in ("B", "L", "T"):
            self._absent(k)
            return True
        log.warning("no motor-board equivalent for %r", line)
        return False

    def _absent(self, k):
        if k in self._warned:
            return
        self._warned.add(k)
        what = {"B": "BUZZER", "L": "INDICATOR LEDS", "T": "SELF-TEST"}[k]
        log.warning("THIS BOARD HAS NO %s: the '%s' command does nothing. A width verdict reaches "
                    "a human through the dashboard and its speech only.", what, k)

    def _write(self, text):
        with self._lock:
            s = self._ser
            if s is None:
                return False
            try:
                s.write((text + "\n").encode())
                self._last_write = time.monotonic()
                return True
            except (serial.SerialException, OSError) as e:
                log.warning("motor write failed: %s", e)
                return False

    # ---- thread ----
    def _run(self):
        while True:
            device = self.fixed_port or self.port_hint
            self.port_hint = None
            if device is None:
                device, fw = ports.find("motor")
                if device:
                    self.fw = fw
            if device:
                self._misses = 0
                try:
                    self._session(device)
                except (serial.SerialException, OSError, ValueError) as e:
                    log.warning("motor board %s: %s", device, e)
                finally:
                    with self._lock:
                        self._ser = None
                    self.connected = False
                    self.drive = None
                    ports.in_use.discard(device)
                    log.error("MOTOR BOARD DISCONNECTED: driving off until it is back")
            else:
                self._misses += 1
                if self._misses == 1 or self._misses % 20 == 0:
                    log.error("MOTOR BOARD NOT FOUND: driving disabled. USB serial ports seen: %s. "
                              "Set SCOUT_MOTOR_PORT=/dev/... to force one.", ports.seen())
            time.sleep(3)

    def _session(self, device):
        with serial.Serial(device, 115200, timeout=0.5, exclusive=True) as s:
            ports.in_use.add(device)
            # Opening the port has just reset the board. Wait for its banner before reporting the
            # link up: anything written while it reboots is lost, and that would silently eat the
            # first drive command of the run.
            deadline = time.monotonic() + BOOT_S
            while time.monotonic() < deadline:
                text = s.readline().decode("utf-8", "replace").strip()
                if BANNER in text:
                    self.fw = text
                    break
            else:
                log.warning("motor board on %s never sent its banner; carrying on anyway", device)

            with self._lock:
                self._ser = s
                self._last_write = time.monotonic()
            self.port = device
            self.connected = True
            log.info("MOTOR  connected on %s (%s)", device, self.fw or "no banner")
            last_reply = time.monotonic()
            while True:
                line = s.readline()
                now = time.monotonic()
                if line:
                    last_reply = now
                    self._reply(line.decode("utf-8", "replace").strip())
                elif now - last_reply > SILENCE_S:
                    raise serial.SerialException(f"no reply for {SILENCE_S:.0f} s")
                # The board is silent unless spoken to, so when we have nothing to say we ask for
                # status and keep silence meaningful. Only when the line is already quiet: a "?"
                # sent while driving could pet the board's watchdog, and that watchdog is the one
                # thing that stops the motors if this process dies.
                if now - self._last_write > KEEPALIVE_S:
                    self._write("?")

    def _reply(self, text):
        if not text:
            return
        if text.startswith("ok "):
            parts = text.split()
            if len(parts) == 3:
                try:
                    v, w = _unmix(int(parts[1]), int(parts[2]))
                except ValueError:
                    return
                self.drive = Drive(round(v, 3), round(w, 3))
            return
        if text == "watchdog stop":
            # Expected every time a human lets go of a key, so this is not a warning.
            self.drive = Drive(0.0, 0.0)
            log.debug("motor watchdog stop")
            return
        if BANNER in text:
            self.fw = text
            log.info("motor board: %s", text)
            return
        if text == "err":
            log.warning("motor board rejected a command")
            return
        log.debug("motor board: %s", text)
