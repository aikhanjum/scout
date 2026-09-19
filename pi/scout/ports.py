"""Find the ESP32 and the lidar among the USB serial ports by probing what answers.
Never by /dev/ttyUSB0: the two devices swap names between boots."""
import json
import logging
import threading
import time

import serial
from serial.tools import list_ports

from . import rplidar

log = logging.getLogger("scout.ports")

in_use = set()              # devices our own threads hold open; never probe those
lock = threading.Lock()     # one probe at a time, so the two device threads never open the same port together


def usb_serial_ports():
    ports = [p for p in list_ports.comports() if p.vid is not None and not p.device.startswith("/dev/tty.")]
    ports.sort(key=lambda p: p.device)
    return ports


def describe(p):
    return f"{p.device} [{(p.manufacturer or '').strip()} {(p.description or '').strip()} vid={p.vid:#06x} pid={p.pid:#06x} sn={p.serial_number}]"


def looks_like_esp32(device, listen_s=3.0):
    """Open the port (which resets a dev board) and listen for the bridge's JSON lines. Returns its fw or None."""
    try:
        with serial.Serial(device, 115200, timeout=0.2, exclusive=True) as s:
            end = time.monotonic() + listen_s
            fw = None
            while time.monotonic() < end:
                line = s.readline()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue            # bootloader chatter or a torn line
                if isinstance(obj, dict) and "hello" in obj:
                    return obj.get("fw", "?")
                if isinstance(obj, dict) and "pitch" in obj:
                    fw = fw or "?"      # already running: keep listening briefly for the hello, else accept
                    end = min(end, time.monotonic() + 0.5)
            return fw
    except (serial.SerialException, OSError) as e:
        log.debug("probe %s as ESP32: %s", device, e)
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
    """kind is 'esp32' or 'lidar'. Returns (device, detail) or (None, None). Marks the device in use."""
    probe = looks_like_esp32 if kind == "esp32" else looks_like_lidar
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
