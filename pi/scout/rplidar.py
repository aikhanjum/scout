# Vendored from ~/dev/lidar-gaps/lidar_gaps/rplidar.py on 2026-09-19 (that project is not a git repo).
# Edit there first, then copy here. Its tests live in ~/dev/lidar-gaps/tests/test_rplidar.py.

"""RPLIDAR driver over pyserial: A1, A2M8 (the one on Scout) and newer models.

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

CMDFLAG_HAS_PAYLOAD = 0x80  # commands with this bit set carry <size><payload><xor>

CMD_STOP = 0x25
CMD_SCAN = 0x20
CMD_RESET = 0x40
CMD_GET_DEVICE_INFO = 0x50
CMD_GET_DEVICE_HEALTH = 0x52
CMD_HQ_MOTOR_SPEED_CTRL = 0xA8
CMD_SET_MOTOR_PWM = 0xF0
CMD_GET_ACC_BOARD_FLAG = 0xFF
CMD_EXPRESS_SCAN = 0x82

ANS_TYPE_DEVINFO = 0x04
ANS_TYPE_DEVHEALTH = 0x06
ANS_TYPE_MEASUREMENT = 0x81
ANS_TYPE_ACC_BOARD_FLAG = 0xFF
ANS_TYPE_MEASUREMENT_CAPSULED = 0x82

CAPSULE_SIZE = 84            # 2 checksum/sync bytes + u16 start angle + 16 cabins of 5 bytes
CABINS_PER_CAPSULE = 16
EXPRESS_SYNC_1 = 0xA         # high nibble of byte 0
EXPRESS_SYNC_2 = 0x5         # high nibble of byte 1
EXPRESS_SYNCBIT = 0x8000     # in start_angle_sync_q6: a new rotation starts here

ACC_BOARD_FLAG_MOTOR_CTRL_SUPPORT = 0x1

# Model-ID thresholds, from sl_lidar_driver.cpp.
A2A3_MIN_MAJOR_ID = 2       # >= this: motor may be driven by the accessory board (PWM)
BUILTIN_MOTORCTL_MIN_MAJOR_ID = 6   # >= this: motor is driven by an RPM command
TOF_C_MIN_MAJOR_ID = 4
TOF_S_MIN_MAJOR_ID = 6
TOF_T_MIN_MAJOR_ID = 9
TOF_M_MIN_MAJOR_ID = 12

# How the motor is started, decided from the model ID and accessory board.
MOTOR_DTR = "dtr"   # A1 and older: motor spins while DTR is low
MOTOR_PWM = "pwm"   # A2/A3 with accessory board: CMD_SET_MOTOR_PWM
MOTOR_RPM = "rpm"   # S/T/C/M series: CMD_HQ_MOTOR_SPEED_CTRL

# The SDK's DEFAULT_MOTOR_PWM is not defined in this SDK copy; 660 is the long-standing
# RPLIDAR default and is what this driver was verified against on an A2M8.
DEFAULT_MOTOR_PWM = 660
MAX_MOTOR_PWM = 1023
DEFAULT_MOTOR_RPM = 600

HEALTH_STATUS = {0: "OK", 1: "WARNING", 2: "ERROR"}

BAUD_A1 = 115200

Point = Tuple[float, float]  # (angle_deg, distance_mm)
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

def payload_packet(cmd: int, payload: bytes = b"") -> bytes:
    """Build a command packet that carries a payload.

    Wire format (sl_lidarprotocol_codec.cpp): A5 <cmd> <size> <payload...> <checksum>,
    where the checksum is the XOR of every preceding byte."""
    body = bytes([SYNC_BYTE, cmd, len(payload)]) + payload
    checksum = 0
    for b in body:
        checksum ^= b
    return body + bytes([checksum])


def model_name(model_id: int) -> str:
    """Human-readable model name, using the SDK's GetModelNameStringByModelID formula."""
    major, minor = model_id >> 4, model_id & 0xF
    for letter, base in (("M", TOF_M_MIN_MAJOR_ID), ("T", TOF_T_MIN_MAJOR_ID),
                         ("S", TOF_S_MIN_MAJOR_ID), ("C", TOF_C_MIN_MAJOR_ID)):
        if major >= base:
            return f"{letter}{major - base + 1}M{minor}"
    return f"A{major}M{minor}"


@dataclass(frozen=True)
class Capsule:
    """One express-scan capsule: a start angle plus 16 cabins of two samples each."""
    start_angle_deg: float
    new_scan: bool
    start_q6: int
    cabins: Tuple[Tuple[int, int, int], ...]   # (distance_angle_1, distance_angle_2, offset_angles_q3)


def parse_capsule(raw: bytes) -> Capsule:
    """Decode one 84-byte express capsule, checking both sync nibbles and the checksum."""
    if len(raw) != CAPSULE_SIZE:
        raise ProtocolError(f"capsule must be {CAPSULE_SIZE} bytes, got {len(raw)}")
    if raw[0] >> 4 != EXPRESS_SYNC_1 or raw[1] >> 4 != EXPRESS_SYNC_2:
        raise ProtocolError(f"bad capsule sync nibbles {raw[0] >> 4:#x} {raw[1] >> 4:#x} (expected a 5)")
    want = (raw[0] & 0xF) | ((raw[1] & 0xF) << 4)
    got = 0
    for b in raw[2:]:
        got ^= b
    if want != got:
        raise ProtocolError(f"capsule checksum {got:#04x} does not match {want:#04x}")
    start_q6 = struct.unpack("<H", raw[2:4])[0]
    cabins = tuple(struct.unpack("<HHB", raw[4 + 5 * i:9 + 5 * i]) for i in range(CABINS_PER_CAPSULE))
    return Capsule(start_angle_deg=(start_q6 & 0x7FFF) / 64.0,
                   new_scan=bool(start_q6 & EXPRESS_SYNCBIT),
                   start_q6=start_q6, cabins=cabins)


def decode_capsule_pair(prev: Capsule, cur: Capsule) -> List[Node]:
    """Expand the earlier capsule into 32 nodes, per _onScanNodeCapsuleData in the SDK.

    Express capsules carry distances plus small per-sample angle offsets, but not
    absolute angles. The angles are interpolated across the span between this
    capsule's start angle and the next one's, which is why decoding always lags
    one capsule behind the stream."""
    cur_start_q8 = (cur.start_q6 & 0x7FFF) << 2
    prev_start_q8 = (prev.start_q6 & 0x7FFF) << 2
    diff_q8 = cur_start_q8 - prev_start_q8
    if prev_start_q8 > cur_start_q8:
        diff_q8 += 360 << 8          # the rotation wrapped past 360 between the two
    inc_q16 = diff_q8 << 3
    angle_q16 = prev_start_q8 << 8

    out: List[Node] = []
    for d_a1, d_a2, offs in prev.cabins:
        for dist_raw, off_q3 in ((d_a1, (offs & 0xF) | ((d_a1 & 0x3) << 4)),
                                 (d_a2, (offs >> 4) | ((d_a2 & 0x3) << 4))):
            angle_q6 = (angle_q16 - (off_q3 << 13)) >> 10
            sync = ((angle_q16 + inc_q16) % (360 << 16)) < inc_q16
            angle_q16 += inc_q16
            if angle_q6 < 0:
                angle_q6 += 360 << 6
            elif angle_q6 >= (360 << 6):
                angle_q6 -= 360 << 6
            dist_q2 = dist_raw & 0xFFFC
            out.append(Node(new_scan=bool(sync), quality=0x2F if dist_q2 else 0,
                            angle_deg=angle_q6 / 64.0, dist_mm=dist_q2 / 4.0))
    return out


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


CP2102_VID = 0x10C4


def parse_ioreg_usb(out: str) -> List[Tuple[int, int, str]]:
    """Parse `ioreg -r -c IOUSBHostDevice -l -w0` output into (vid, pid, name)."""
    import re

    found = []
    for block in re.split(r"^\s*\+-o ", out, flags=re.M)[1:]:
        vid = re.search(r'"idVendor" = (\d+)', block)
        pid = re.search(r'"idProduct" = (\d+)', block)
        name = re.search(r'"USB Product Name" = "([^"]*)"', block)
        if vid and pid:
            found.append((int(vid.group(1)), int(pid.group(1)), name.group(1) if name else "?"))
    return found


def usb_devices() -> List[Tuple[int, int, str]]:
    """(vid, pid, name) for every USB device the OS has enumerated.

    macOS: parsed from `ioreg`. Linux: read from /sys/bus/usb/devices.
    Any failure returns [] so the diagnosis degrades gracefully."""
    import glob
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-w0"],
                                 capture_output=True, text=True, timeout=10).stdout
            return parse_ioreg_usb(out)
        found = []
        for d in glob.glob("/sys/bus/usb/devices/*"):
            try:
                with open(f"{d}/idVendor") as f:
                    vid = int(f.read().strip(), 16)
                with open(f"{d}/idProduct") as f:
                    pid = int(f.read().strip(), 16)
            except (OSError, ValueError):
                continue
            try:
                with open(f"{d}/product") as f:
                    name = f.read().strip()
            except OSError:
                name = "?"
            if vid != 0x1D6B:  # skip Linux root hubs
                found.append((vid, pid, name))
        return found
    except Exception:
        return []


def diagnose_no_port(usb_devices: List[Tuple[int, int, str]], serial_ports: List[str]) -> str:
    """Explain a failed port probe in terms of which layer is missing."""
    lines = ["No RPLIDAR serial port found."]
    adapters = [d for d in usb_devices if d[0] == CP2102_VID]
    if adapters:
        names = ", ".join(f"{n} ({v:04x}:{p:04x})" for v, p, n in adapters)
        lines += [
            f"  The USB adapter IS enumerated: {names}",
            "  but no serial device was created for it, so this is a driver problem.",
            "  macOS 13+ has a built-in CP210x driver; if it did not bind, install the Silicon Labs",
            "  driver from vendor/rplidar_sdk-master/tools/cp2102_driver and re-plug.",
            "  On Linux: check `dmesg | tail` for cp210x, and that your user is in the dialout group.",
        ]
    elif usb_devices:
        names = ", ".join(f"{n} ({v:04x}:{p:04x})" for v, p, n in usb_devices)
        lines += [
            f"  The OS sees these USB devices: {names}",
            "  but no CP2102 UART adapter (VID 10c4) among them. Either the LIDAR is on a",
            "  different port/hub or its adapter board is not getting a data connection.",
        ]
    else:
        lines += [
            "  The OS sees no USB devices at all, so the LIDAR's USB adapter never enumerated.",
            "  This is below the software layer: a charge-only cable, a hub/adapter that is",
            "  not passing data, or the port/cable not seated. Try another cable and port;",
            "  the adapter's LED should light and the device should appear before any driver matters.",
        ]
    lines += [
        f"  Serial ports present: {serial_ports or 'none'}",
        "  Once the device shows up, you can also pass the port explicitly:",
        "    tools/lidar-test --port /dev/cu.usbserial-XXXX   (Mac)",
        "    tools/lidar-test --port /dev/ttyUSB0             (Pi)",
    ]
    return "\n".join(lines)


def find_port() -> str:
    ports = candidate_ports()
    if not ports:
        raise LidarNotFound(diagnose_no_port(usb_devices(), all_ports()))
    return ports[0][0]


class RPLidar:
    """Driver for the RPLIDAR A-series (and newer) over a serial port.

    How the motor is started depends on the model, exactly as the SDK decides it in
    checkMotorCtrlSupport(): an A1 has no motor command and spins while DTR is low,
    an A2/A3 on an accessory board needs CMD_SET_MOTOR_PWM, and the S/T/C/M series
    take an RPM command. Getting this wrong looks like a healthy device that answers
    every query and then streams no scan data at all."""

    def __init__(self, port: str, baud: int = BAUD_A1, timeout_s: float = 1.0,
                 read_timeout_s: float = 5.0):
        import serial

        try:
            ser = serial.Serial(port, baud, timeout=timeout_s)
        except serial.SerialException as e:
            raise LidarNotFound(f"Could not open serial port {port}: {e}") from e
        self._init(ser, port, read_timeout_s)

    @classmethod
    def from_serial(cls, ser, port: str = "<serial>", read_timeout_s: float = 5.0) -> "RPLidar":
        """Wrap an already-open serial-like object. Used by the tests."""
        self = cls.__new__(cls)
        self._init(ser, port, read_timeout_s)
        return self

    def _init(self, ser, port: str, read_timeout_s: float) -> None:
        self.ser = ser
        self.port = port
        self.read_timeout_s = read_timeout_s
        self._stream_open = False
        self.scan_mode = "none"        # "standard" | "express" once a scan is running
        self._info: Optional[DeviceInfo] = None
        self._motor_support: Optional[str] = None
        self._motor_running = False
        self.ser.dtr = False  # DTR low = motor on for models with no motor command

    # -- low level -------------------------------------------------------
    def _send(self, cmd: int) -> None:
        self.ser.write(bytes([SYNC_BYTE, cmd]))
        self.ser.flush()

    def _send_payload(self, cmd: int, payload: bytes) -> None:
        self.ser.write(payload_packet(cmd, payload))
        self.ser.flush()

    def _read_exact(self, n: int, what: str, timeout_s: Optional[float] = None) -> bytes:
        """Read exactly n bytes, accumulating across short reads until the deadline.

        A single ser.read() can come back short or empty while the motor is still
        spinning up, so a one-shot read is not enough to declare a timeout."""
        deadline = time.monotonic() + (self.read_timeout_s if timeout_s is None else timeout_s)
        buf = bytearray()
        while len(buf) < n:
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf += chunk
                continue
            if time.monotonic() >= deadline:
                break
        if len(buf) != n:
            raise ProtocolError(
                f"timed out reading {what}: wanted {n} bytes, got {len(buf)} from {self.port}. "
                "Is the motor actually spinning? Check the motor-control mode for this model."
            )
        return bytes(buf)

    def _request(self, cmd: int, expect_type: int, payload: bytes = b"") -> bytes:
        self.ser.reset_input_buffer()
        if payload or (cmd & CMDFLAG_HAS_PAYLOAD):
            self._send_payload(cmd, payload)
        else:
            self._send(cmd)
        hdr = parse_response_header(self._read_exact(HEADER_SIZE, "response header"))
        if hdr.type != expect_type:
            raise ProtocolError(f"expected response type {expect_type:#04x}, got {hdr.type:#04x}")
        return self._read_exact(hdr.size, "response payload")

    # -- device queries --------------------------------------------------
    def get_info(self, refresh: bool = False) -> DeviceInfo:
        if self._info is None or refresh:
            p = self._request(CMD_GET_DEVICE_INFO, ANS_TYPE_DEVINFO)
            model, fw, hw = struct.unpack("<BHB", p[:4])
            self._info = DeviceInfo(model=model, firmware=f"{fw >> 8}.{fw & 0xFF:02d}",
                                    hardware=hw, serial=p[4:20].hex().upper())
        return self._info

    def get_health(self) -> Tuple[str, int]:
        p = self._request(CMD_GET_DEVICE_HEALTH, ANS_TYPE_DEVHEALTH)
        status, err = struct.unpack("<BH", p[:3])
        return HEALTH_STATUS.get(status, f"UNKNOWN({status})"), err

    def accessory_board_has_motor_control(self) -> bool:
        """True if the accessory board can drive the motor over CMD_SET_MOTOR_PWM."""
        try:
            p = self._request(CMD_GET_ACC_BOARD_FLAG, ANS_TYPE_ACC_BOARD_FLAG,
                              struct.pack("<I", 0))
        except ProtocolError:
            return False
        if len(p) < 4:
            return False
        return bool(struct.unpack("<I", p[:4])[0] & ACC_BOARD_FLAG_MOTOR_CTRL_SUPPORT)

    def motor_control_mode(self) -> str:
        """Which of MOTOR_DTR / MOTOR_PWM / MOTOR_RPM this device needs."""
        if self._motor_support is None:
            major = self.get_info().model >> 4
            if major >= BUILTIN_MOTORCTL_MIN_MAJOR_ID:
                self._motor_support = MOTOR_RPM
            elif major >= A2A3_MIN_MAJOR_ID and self.accessory_board_has_motor_control():
                self._motor_support = MOTOR_PWM
            else:
                self._motor_support = MOTOR_DTR
        return self._motor_support

    # -- motor -----------------------------------------------------------
    def set_motor(self, on: bool) -> None:
        if not on and self._motor_support is None:
            # Never started it, so there is nothing to command. Asking the device which
            # motor mode it wants would mean more I/O on a port that may not be a lidar
            # at all, which is exactly what port probing does when it releases a port.
            self.ser.dtr = True
            return
        mode = self.motor_control_mode()
        if mode == MOTOR_RPM:
            self._send_payload(CMD_HQ_MOTOR_SPEED_CTRL,
                               struct.pack("<H", DEFAULT_MOTOR_RPM if on else 0))
        elif mode == MOTOR_PWM:
            self._send_payload(CMD_SET_MOTOR_PWM,
                               struct.pack("<H", DEFAULT_MOTOR_PWM if on else 0))
        else:
            self.ser.dtr = not on
        self._motor_running = on

    def motor_on(self) -> None:
        self.set_motor(True)

    def motor_off(self) -> None:
        self.set_motor(False)

    # -- scanning --------------------------------------------------------
    def stop(self) -> None:
        self._send(CMD_STOP)
        self._stream_open = False
        self.scan_mode = "none"
        self.ser.reset_input_buffer()

    def reset(self) -> None:
        self._send(CMD_RESET)
        time.sleep(0.5)  # firmware reboot, per the SDK
        self.ser.reset_input_buffer()

    def start_scan(self, express: bool = True) -> None:
        """Start scanning, preferring express mode.

        Express doubles the sample rate on an A2 by packing 32 samples into an
        84-byte capsule. Not every model or firmware answers it, so a device that
        replies with anything but a capsule header falls back to the standard
        5-byte node stream rather than failing."""
        self.motor_on()
        if express and self._try_start_express():
            return
        self.ser.reset_input_buffer()
        self._send(CMD_SCAN)
        hdr = parse_response_header(self._read_exact(HEADER_SIZE, "scan response header"))
        if hdr.type != ANS_TYPE_MEASUREMENT or hdr.size != NODE_SIZE:
            raise ProtocolError(f"unexpected scan response: type={hdr.type:#04x} size={hdr.size}")
        self.scan_mode = "standard"
        self._stream_open = True

    def _try_start_express(self) -> bool:
        self.ser.reset_input_buffer()
        self._send_payload(CMD_EXPRESS_SCAN, struct.pack("<BHH", 0, 0, 0))
        try:
            hdr = parse_response_header(self._read_exact(HEADER_SIZE, "express scan response header"))
        except ProtocolError:
            return False
        if hdr.type != ANS_TYPE_MEASUREMENT_CAPSULED or hdr.size != CAPSULE_SIZE:
            # Not supported. Drain whatever it did start sending before falling back.
            self.stop()
            time.sleep(0.05)
            self.ser.reset_input_buffer()
            return False
        self.scan_mode = "express"
        self._stream_open = True
        return True

    def _express_nodes(self) -> Iterator[Node]:
        """Capsules in, nodes out. Decoding lags one capsule, since angles are
        interpolated between consecutive capsule start angles."""
        prev: Optional[Capsule] = None
        while True:
            raw = self._read_exact(CAPSULE_SIZE, "express capsule")
            try:
                cap = parse_capsule(raw)
            except ProtocolError:
                # Re-sync on the two sync nibbles rather than give up on the stream.
                prev = None
                buf = bytearray(raw)
                while True:
                    buf = buf[1:] + self._read_exact(1, "capsule resync byte")
                    if buf[0] >> 4 == EXPRESS_SYNC_1 and buf[1] >> 4 == EXPRESS_SYNC_2:
                        try:
                            cap = parse_capsule(bytes(buf))
                            break
                        except ProtocolError:
                            continue
            if cap.new_scan:
                prev = None
            if prev is not None:
                for n in decode_capsule_pair(prev, cap):
                    yield n
            elif cap.new_scan:
                # The rotation starts here; mark it so rotations() can split on it.
                prev = cap
                continue
            prev = cap

    def nodes(self) -> Iterator[Node]:
        """Stream nodes forever. Re-syncs on the sync/inverse bits if bytes slip."""
        if not self._stream_open:
            self.start_scan()
        if self.scan_mode == "express":
            for n in self._express_nodes():
                yield n
            return
        while True:
            raw = self._read_exact(NODE_SIZE, "scan node")
            try:
                yield parse_node(raw)
            except ProtocolError:
                buf = bytearray(raw)
                while True:
                    buf = buf[1:] + self._read_exact(1, "resync byte")
                    try:
                        yield parse_node(bytes(buf))
                        break
                    except ProtocolError:
                        continue

    def read_rotation(self, settle_rotations: int = 6, max_nodes: int = 20000) -> List[Point]:
        """Block until a complete rotation has been received at a settled speed.

        The sample rate is fixed, so while the motor is spinning up each rotation
        takes longer and yields more points. Measured on an A2M8, the period fell
        from 140 ms to a steady 100 ms over the first six rotations, so that many
        are read and discarded before one is returned."""
        def bounded():
            for i, n in enumerate(self.nodes()):
                if i >= max_nodes:
                    raise ProtocolError(
                        f"read {max_nodes} nodes without completing a rotation; "
                        "is the motor spinning?")
                yield n
        for i, rot in enumerate(rotations(bounded())):
            if i >= settle_rotations:
                return rot
        raise ProtocolError("scan stream ended before a full rotation")

    def close(self) -> None:
        try:
            self.stop()
            self.motor_off()
        finally:
            self.ser.close()

    def __enter__(self) -> "RPLidar":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


RPLidarA1 = RPLidar  # the original name, kept so older callers keep working
