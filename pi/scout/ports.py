"""Find the motor board and the lidar among the USB serial ports by probing what answers.
Never by /dev/ttyUSB0: the two devices swap names between boots, and on the Pi both arrive as
/dev/ttyUSB* with vendor IDs that do not reliably tell them apart. Probing by content is why
Scout needs no udev rule and survives the two being plugged in either order."""
import json
import logging
import threading
import time

import serial
from serial.tools import list_ports

from . import rplidar

log = logging.getLogger("scout.ports")

MOTOR_BANNER = "scoutable-motor"   # the RedBoard firmware's boot banner, and how we recognise it

in_use = set()              # devices our own threads hold open; never probe those
lock = threading.Lock()     # one probe at a time, so the two device threads never open the same port together


def usb_serial_ports():
    ports = [p for p in list_ports.comports() if p.vid is not None and not p.device.startswith("/dev/tty.")]
    ports.sort(key=lambda p: p.device)
    return ports


def describe(p):
    return f"{p.device} [{(p.manufacturer or '').strip()} {(p.description or '').strip()} vid={p.vid:#06x} pid={p.pid:#06x} sn={p.serial_number}]"


def looks_like_motor(device, listen_s=4.0):
    """Open the port, which resets the board over DTR, and wait for its boot banner.

    Returns the banner line or None. The banner is the whole test: the RedBoard's CH340 and the
    lidar's USB adapter can carry the same vendor ID as each other's, and both enumerate as
    /dev/ttyUSB*, so the only trustworthy question is what answers. A lidar opened at 115200
    returns noise that does not contain the banner, which is the correct answer for it.
    """
    try:
        with serial.Serial(device, 115200, timeout=0.5, exclusive=True) as s:
            end = time.monotonic() + listen_s
            while time.monotonic() < end:
                line = s.readline()
                if not line:
                    continue
                text = line.decode("utf-8", "replace").strip()
                if MOTOR_BANNER in text:
                    return text
            return None
    except (serial.SerialException, OSError) as e:
        log.debug("probe %s as motor board: %s", device, e)
        return None


def looks_like_lidar(device):
    """Ask for device info the way the SDK does. Returns DeviceInfo or None."""
    try:
        lidar = rplidar.RPLidar(device, timeout_s=1.0)
    except rplidar.LidarNotFound as e:
        log.debug("probe %s as lidar: %s", device, e)
        return None
    try:
        return lidar.get_info()
    except (rplidar.ProtocolError, serial.SerialException, OSError) as e:
        log.debug("probe %s as lidar: %s", device, e)
        return None
    finally:
        try:
            lidar.close()
        except Exception:
            pass


def find(kind):
    """kind is 'motor' or 'lidar'. Returns (device, detail) or (None, None). Marks the device in use."""
    probe = looks_like_motor if kind == "motor" else looks_like_lidar
    with lock:
        for p in usb_serial_ports():
            if p.device in in_use:
                continue
            found = probe(p.device)
            if found:
                in_use.add(p.device)
                return p.device, found
        return None, None


def seen():
    return [describe(p) for p in usb_serial_ports()] or ["none"]
