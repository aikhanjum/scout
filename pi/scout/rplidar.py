# Vendored from ~/dev/lidar-gaps/lidar_gaps/rplidar.py on 2026-09-19 (that project is not a git repo).
# Edit there first, then copy here. Its tests live in ~/dev/lidar-gaps/tests/test_rplidar.py.

"""Minimal RPLIDAR A1 driver over pyserial.

Protocol details come straight from the Slamtec SDK headers
(sdk/include/sl_lidar_protocol.h and sl_lidar_cmd.h):

  request : A5 <cmd>                       (no payload)
  response: A5 5A <size:30|subtype:2 LE u32> <type>   (7 bytes)
  scan node (5 bytes, type 0x81, streamed forever after CMD_SCAN):
      byte0    sync:1 | sync_inverse:1 | quality:6
      byte1-2  check_bit:1 | angle_q6:15   (little endian)
      byte3-4  distance_q2                 (little endian, 0 = no return)

The A1 has no motor command. On the standard USB adapter the motor spins
while DTR is low; the SDK clears DTR right after opening the port and sets
it again to stop. pyserial exposes that as ser.dtr, on every OS.
"""
import struct
import time
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple

SYNC_BYTE = 0xA5
ANS_SYNC1, ANS_SYNC2 = 0xA5, 0x5A

CMD_STOP = 0x25
CMD_SCAN = 0x20
CMD_RESET = 0x40
CMD_GET_DEVICE_INFO = 0x50
CMD_GET_DEVICE_HEALTH = 0x52

ANS_TYPE_DEVINFO = 0x04
ANS_TYPE_DEVHEALTH = 0x06
ANS_TYPE_MEASUREMENT = 0x81

HEALTH_STATUS = {0: "OK", 1: "WARNING", 2: "ERROR"}

BAUD_A1 = 115200
NODE_SIZE = 5
HEADER_SIZE = 7


class ProtocolError(Exception):
    """Bytes on the wire did not look like the RPLIDAR protocol."""


class LidarNotFound(Exception):
    """No serial port that looks like an RPLIDAR could be found."""


@dataclass(frozen=True)
class Node:
    new_scan: bool
    quality: int
    angle_deg: float
    dist_mm: float


@dataclass(frozen=True)
class ResponseHeader:
    type: int
    size: int
    subtype: int

    @property
    def is_stream(self) -> bool:
        return bool(self.subtype & 0x1)


@dataclass(frozen=True)
class DeviceInfo:
    model: int
    firmware: str
    hardware: int
    serial: str


# ---------------------------------------------------------------- pure parsing

def parse_node(raw: bytes) -> Node:
    if len(raw) != NODE_SIZE:
        raise ProtocolError(f"node must be {NODE_SIZE} bytes, got {len(raw)}")
    b0, angle_check, dist_q2 = struct.unpack("<BHH", raw)
    sync, inv = b0 & 0x1, (b0 >> 1) & 0x1
    if sync == inv:
        raise ProtocolError(f"bad sync bits in node byte0={b0:#04x}")
    if not angle_check & 0x1:
        raise ProtocolError("check bit clear in node angle field")
    return Node(
        new_scan=bool(sync),
        quality=b0 >> 2,
        angle_deg=(angle_check >> 1) / 64.0,
        dist_mm=dist_q2 / 4.0,
    )


def parse_response_header(raw: bytes) -> ResponseHeader:
    if len(raw) != HEADER_SIZE:
        raise ProtocolError(f"header must be {HEADER_SIZE} bytes, got {len(raw)}")
    s1, s2, size_sub, typ = struct.unpack("<BBIB", raw)
    if (s1, s2) != (ANS_SYNC1, ANS_SYNC2):
        raise ProtocolError(f"bad response sync {s1:#04x} {s2:#04x} (expected a5 5a)")
    return ResponseHeader(type=typ, size=size_sub & 0x3FFFFFFF, subtype=size_sub >> 30)


def rotations(nodes: Iterable[Node]) -> Iterator[List[Tuple[float, float]]]:
    """Group a node stream into full rotations of (angle_deg, dist_mm).

    The stream starts mid-rotation, so everything before the first sync
    flag is discarded. A rotation is yielded when the next sync arrives."""
    current: Optional[List[Tuple[float, float]]] = None
    for n in nodes:
        if n.new_scan:
            if current:
                yield current
            current = []
        if current is not None:
            current.append((n.angle_deg, n.dist_mm))


# -------------------------------------------------------------- serial driver

def candidate_ports() -> List[Tuple[str, str]]:
    """(device, description) for every serial port that could be an RPLIDAR.

    The A1's USB adapter is a Silicon Labs CP2102 (VID 0x10C4). We accept that
    VID, or anything that looks like a USB serial adapter as a fallback."""
    from serial.tools import list_ports

    found = []
    for p in list_ports.comports():
        desc = f"{p.description or ''} {p.manufacturer or ''}".strip()
        cp2102 = p.vid == 0x10C4
        usbish = any(k in (p.device + " " + desc).lower()
                     for k in ("usbserial", "slab", "cp210", "ttyusb", "ttyacm", "usbmodem"))
        if cp2102 or usbish:
            found.append((p.device, desc))
    # Prefer /dev/cu.* over /dev/tty.* on macOS: the cu device does not block on carrier.
    found.sort(key=lambda d: (not d[0].startswith("/dev/cu."), d[0]))
    return found


def all_ports() -> List[str]:
    from serial.tools import list_ports

    return [p.device for p in list_ports.comports()]


def find_port() -> str:
    ports = candidate_ports()
    if not ports:
        raise LidarNotFound(
            "No RPLIDAR serial port found.\n"
            "  Looking for a Silicon Labs CP2102 USB adapter (VID 0x10C4) or a usbserial/ttyUSB device.\n"
            f"  Serial ports present: {all_ports() or 'none'}\n"
            "  Check the USB cable is plugged in, then pass the port explicitly:\n"
            "    tools/lidar-test --port /dev/cu.usbserial-XXXX   (Mac)\n"
            "    tools/lidar-test --port /dev/ttyUSB0             (Pi)\n"
            "  On macOS you may need the CP210x driver from vendor/.../tools/cp2102_driver."
        )
    return ports[0][0]


class RPLidarA1:
    def __init__(self, port: str, baud: int = BAUD_A1, timeout_s: float = 1.0):
        import serial

        try:
            self.ser = serial.Serial(port, baud, timeout=timeout_s)
        except serial.SerialException as e:
            raise LidarNotFound(f"Could not open serial port {port}: {e}") from e
        self.port = port
        self.ser.dtr = False  # DTR low = motor on (matches the SDK's clearDTR after open)
        self._stream_open = False

    # -- low level -------------------------------------------------------
    def _send(self, cmd: int) -> None:
        self.ser.write(bytes([SYNC_BYTE, cmd]))
        self.ser.flush()

    def _read_exact(self, n: int, what: str) -> bytes:
        data = self.ser.read(n)
        if len(data) != n:
            raise ProtocolError(
                f"timed out reading {what}: wanted {n} bytes, got {len(data)} "
                f"from {self.port}. Is this really the LIDAR, and is the motor powered?"
            )
        return data

    def _request(self, cmd: int, expect_type: int) -> bytes:
        self.ser.reset_input_buffer()
        self._send(cmd)
        hdr = parse_response_header(self._read_exact(HEADER_SIZE, "response header"))
        if hdr.type != expect_type:
            raise ProtocolError(f"expected response type {expect_type:#04x}, got {hdr.type:#04x}")
        return self._read_exact(hdr.size, "response payload")

    # -- commands --------------------------------------------------------
    def stop(self) -> None:
        self._send(CMD_STOP)
        time.sleep(0.01)
        self._stream_open = False
        self.ser.reset_input_buffer()

    def reset(self) -> None:
        self._send(CMD_RESET)
        time.sleep(0.5)  # firmware reboot, per SDK
        self.ser.reset_input_buffer()

    def motor_off(self) -> None:
        self.ser.dtr = True

    def get_info(self) -> DeviceInfo:
        p = self._request(CMD_GET_DEVICE_INFO, ANS_TYPE_DEVINFO)
        model, fw, hw = struct.unpack("<BHB", p[:4])
        return DeviceInfo(model=model, firmware=f"{fw >> 8}.{fw & 0xFF:02d}",
                          hardware=hw, serial=p[4:20].hex().upper())

    def get_health(self) -> Tuple[str, int]:
        p = self._request(CMD_GET_DEVICE_HEALTH, ANS_TYPE_DEVHEALTH)
        status, err = struct.unpack("<BH", p[:3])
        return HEALTH_STATUS.get(status, f"UNKNOWN({status})"), err

    def start_scan(self) -> None:
        self.ser.reset_input_buffer()
        self._send(CMD_SCAN)
        hdr = parse_response_header(self._read_exact(HEADER_SIZE, "scan response header"))
        if hdr.type != ANS_TYPE_MEASUREMENT or hdr.size != NODE_SIZE:
            raise ProtocolError(f"unexpected scan response: type={hdr.type:#04x} size={hdr.size}")
        self._stream_open = True

    def nodes(self) -> Iterator[Node]:
        """Stream nodes forever. Re-syncs on the sync/inverse bits if bytes slip."""
        if not self._stream_open:
            self.start_scan()
        while True:
            raw = self._read_exact(NODE_SIZE, "scan node")
            try:
                yield parse_node(raw)
            except ProtocolError:
                # Shift one byte at a time until we find a plausible node start.
                buf = bytearray(raw)
                while True:
                    buf = buf[1:] + self._read_exact(1, "resync byte")
                    try:
                        yield parse_node(bytes(buf))
                        break
                    except ProtocolError:
                        continue

    def read_rotation(self, max_nodes: int = 20000) -> List[Tuple[float, float]]:
        """Block until one complete rotation has been received.

        Each serial read is bounded by the port timeout; max_nodes bounds the
        wait for a sync flag (the A1 sends ~360-720 nodes per rotation)."""
        def bounded():
            for i, n in enumerate(self.nodes()):
                if i >= max_nodes:
                    raise ProtocolError(
                        f"read {max_nodes} nodes without seeing two rotation sync flags; "
                        "is the motor spinning? (DTR low, motor powered)")
                yield n
        for rot in rotations(bounded()):
            return rot
        raise ProtocolError("scan stream ended unexpectedly")

    def close(self) -> None:
        try:
            self.stop()
            self.motor_off()
        finally:
            self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
