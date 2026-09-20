#!/usr/bin/env python3
"""Flash an Intel HEX onto the RedBoard over its bootloader. No avrdude needed.

    python3 pi/tools/flash_redboard.py firmware.hex [--port /dev/ttyUSB0] [--no-verify]

The Pi has no avrdude and no sudo to apt-get one, so this speaks STK500v1 (what the ATmega328P's
bootloader runs) directly: sync, enter programming mode, write 128-byte pages, read them back,
leave programming mode.

THE PORT IS PROBED, NOT ASSUMED. The lidar and the motor board both arrive as /dev/ttyUSB* and
swap names between boots; writing firmware to the lidar because it happened to be ttyUSB0 is a
mistake this file exists to prevent. --port is for when the board has no working firmware left to
announce itself with.

Stop the scout service first, or it will hold the port:  sudo systemctl stop scout
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import serial                                    # noqa: E402
from scout import ports                          # noqa: E402

PAGE = 128                                       # ATmega328P flash page, in bytes
OK, IN_SYNC = 0x10, 0x14                         # STK500v1 replies bracket every answer


def read_hex(path):
    """Intel HEX -> one flat bytes image. Only records 00 (data) and 01 (EOF) ever appear here."""
    out = bytearray()
    for n, raw in enumerate(open(path), 1):
        raw = raw.strip()
        if not raw or raw[0] != ":":
            continue
        b = bytes.fromhex(raw[1:])
        count, addr, kind = b[0], (b[1] << 8) | b[2], b[3]
        if (sum(b) & 0xFF) != 0:
            sys.exit("line %d: bad checksum" % n)
        if kind == 1:
            break
        if kind != 0:
            sys.exit("line %d: record type %02x is not supported" % (n, kind))
        if addr > len(out):
            out.extend(b"\xff" * (addr - len(out)))   # erased flash reads as 0xff
        out[addr:addr + count] = b[4:4 + count]
    return bytes(out)


class Programmer:
    def __init__(self, port):
        self.s = serial.Serial(port, 115200, timeout=1)

    def reset(self):
        """Pulse DTR/RTS. The auto-reset capacitor turns the edge into a reset on the ATmega."""
        for level in (False, True, False):
            self.s.dtr = level
            self.s.rts = level
            time.sleep(0.12)
        time.sleep(0.05)
        self.s.reset_input_buffer()

    def cmd(self, body, want=0):
        """Every STK500v1 exchange is <body> 0x20 -> 0x14 [payload] 0x10."""
        self.s.write(body + b"\x20")
        self.s.flush()
        head = self.s.read(1)
        if head != bytes([IN_SYNC]):
            raise IOError("no sync, got %r" % head)
        data = self.s.read(want) if want else b""
        if self.s.read(1) != bytes([OK]):
            raise IOError("no ok after %r" % body[:1])
        return data

    def sync(self, tries=6):
        """The bootloader misses the first attempt often enough that one try is not a test."""
        for i in range(tries):
            self.reset()
            try:
                self.cmd(b"0")
                return i + 1
            except IOError:
                continue
        raise SystemExit("board never synced: is it in the bootloader, and is the port right?")

    def page_addr(self, byte_addr):
        w = byte_addr >> 1                        # STK500 addresses flash in WORDS, not bytes
        self.cmd(b"U" + bytes([w & 0xFF, (w >> 8) & 0xFF]))

    def write_page(self, addr, chunk):
        self.page_addr(addr)
        self.cmd(b"d" + bytes([len(chunk) >> 8, len(chunk) & 0xFF]) + b"F" + chunk)

    def read_page(self, addr, n):
        self.page_addr(addr)
        return self.cmd(b"t" + bytes([n >> 8, n & 0xFF]) + b"F", want=n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hex")
    ap.add_argument("--port", default=None, help="skip probing (use when the board has no firmware)")
    ap.add_argument("--no-verify", action="store_true")
    a = ap.parse_args()

    image = read_hex(a.hex)
    print("%s: %d bytes" % (a.hex, len(image)))

    if a.port:
        port, banner = a.port, None
    else:
        port, banner = ports.find("motor")       # returns (device, banner), or (None, None)
    if not port:
        sys.exit("motor board not found. Ports seen: %s. Pass --port if it has no firmware."
                 % (ports.seen(),))
    print("port  : %s%s" % (port, "" if a.port else " (probed, said %r)" % banner))

    p = Programmer(port)
    print("sync  : attempt %d" % p.sync())
    p.cmd(b"P")                                   # enter programming mode
    try:
        for addr in range(0, len(image), PAGE):
            p.write_page(addr, image[addr:addr + PAGE])
            print("\rwrite : %d/%d bytes" % (min(addr + PAGE, len(image)), len(image)), end="")
        print()
        if not a.no_verify:
            bad = 0
            for addr in range(0, len(image), PAGE):
                want = image[addr:addr + PAGE]
                got = p.read_page(addr, len(want))
                if got != want:
                    bad += 1
                    print("\nverify: MISMATCH at 0x%04x" % addr)
                print("\rverify: %d/%d bytes" % (min(addr + PAGE, len(image)), len(image)), end="")
            print()
            if bad:
                sys.exit("verify failed on %d page(s); the board is NOT running the new firmware" % bad)
            print("verify: ok")
    finally:
        try:
            p.cmd(b"Q")                           # leave programming mode, start the sketch
        except IOError:
            pass
    time.sleep(0.5)
    p.s.reset_input_buffer()
    p.reset()
    time.sleep(2.5)                               # the sketch prints its banner after boot
    print("banner: %r" % p.s.read(120))


if __name__ == "__main__":
    main()
