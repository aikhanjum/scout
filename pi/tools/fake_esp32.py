#!/usr/bin/env python3
"""A fake ESP32 bridge on a pty, for developing the Pi service without hardware.

    .venv/bin/python tools/fake_esp32.py
    SCOUT_ESP32_PORT=/dev/ttysNNN .venv/bin/python -m scout      (it prints the exact line)

Streams PROTOCOL.md section 9 JSON lines at 10 Hz, logs every command, and enforces the 500 ms
teleop watchdog the way the real board does. There is no IMU on Scout, so there is nothing to fake
but the drive echo: what the Pi senses comes from the lidar and the camera.
"""
import json
import os
import pty
import select
import termios
import time

master, slave = pty.openpty()
name = os.ttyname(slave)
# A pty echoes by default, so everything this script writes comes straight back at it and it spends
# its life parsing its own telemetry as commands. A real UART does not do that.
attrs = termios.tcgetattr(slave)
attrs[3] &= ~termios.ECHO
attrs[1] &= ~termios.ONLCR          # and do not rewrite \n as \r\n on the way out
termios.tcsetattr(slave, termios.TCSANOW, attrs)
os.set_blocking(master, False)
print(f"fake ESP32 on {name}")
print(f"run:  SCOUT_ESP32_PORT={name} .venv/bin/python -m scout", flush=True)

v = w = 0.0
last_drive = None
watchdog_logged = False
buf = b""
t0 = time.monotonic()


def out(obj):
    try:
        os.write(master, (json.dumps(obj) + "\n").encode())
    except BlockingIOError:
        pass    # nobody has opened the port yet; drop the line like a real UART would


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def handle(line):
    global v, w, last_drive, watchdog_logged
    parts = line.split()
    if not parts:
        return
    c = parts[0]
    if c == "D" and len(parts) == 3:
        v, w = float(parts[1]), float(parts[2])
        last_drive = time.monotonic()
        watchdog_logged = False
    elif c == "S":
        v = w = 0.0
        last_drive = None
        log("cmd S (stop)")
    elif c == "B":
        log(f"cmd B (beep {'fail' if parts[1:2] == ['1'] else 'pass'})")
    elif c == "L" and len(parts) == 3:
        log(f"cmd L (red {parts[1]} green {parts[2]})")
    elif c == "T":
        log("cmd T (self-test, 3 s)")
        out({"test": "start"})
        time.sleep(3)
        out({"test": "done"})
    else:
        log(f"cmd unknown: {line!r}")
        out({"err": f"unknown cmd {c}"})


def hello():
    out({"hello": "scout-esp32", "fw": "fake-0.2.0"})


hello()
log("streaming at 10 Hz")
drive_count = 0
seen_command = False
last_telem = last_summary = last_hello = time.monotonic()

while True:
    r, _, _ = select.select([master], [], [], 0.02)
    if r:
        try:
            buf += os.read(master, 4096)
        except OSError:
            pass
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            text = line.decode("utf-8", "replace").strip()
            if text.startswith("D "):
                drive_count += 1
            seen_command = True
            handle(text)

    now = time.monotonic()
    # Opening the real port resets the board over DTR and it says hello again. A pty cannot be
    # reset, so repeat it until somebody is clearly listening, or the Pi never learns the version.
    if not seen_command and now - last_hello >= 2.0:
        last_hello = now
        hello()
    if last_drive and now - last_drive > 0.5:
        v = w = 0.0
        last_drive = None
        if not watchdog_logged:
            log("watchdog: no D for 500 ms, motors stopped")
            watchdog_logged = True
    if now - last_telem >= 0.1:
        last_telem = now
        out({"t": int((now - t0) * 1000), "v": round(v, 2), "w": round(w, 2)})
    if now - last_summary >= 1.0:            # D arrives at 10 Hz, so summarise it once a second
        last_summary = now
        if drive_count:
            log(f"drive x{drive_count}  v={v} w={w}")
            drive_count = 0
