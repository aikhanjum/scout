#!/usr/bin/env python3
"""A fake ESP32 bridge on a pty, for developing the Pi service without hardware.

    .venv/bin/python tools/fake_esp32.py
    SCOUT_ESP32_PORT=/dev/ttysNNN SCOUT_LIDAR_PORT=none .venv/bin/python -m scout     (it prints the exact line)

Streams PROTOCOL.md section 8 JSON lines at 10 Hz. Every 12 s the pitch climbs to 7.1 degrees for 4 s and comes
back, so the slope audit fires. Logs every command, simulates the 500 ms watchdog, answers Z."""
import json
import os
import pty
import select
import sys
import time

master, slave = pty.openpty()
name = os.ttyname(slave)
os.set_blocking(master, False)
print(f"fake ESP32 on {name}")
print(f"run:  SCOUT_ESP32_PORT={name} SCOUT_LIDAR_PORT=none .venv/bin/python -m scout", flush=True)

v = w = 0.0
zero = 0.0
last_drive = None
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
    global v, w, last_drive, zero
    parts = line.split()
    if not parts:
        return
    c = parts[0]
    if c == "D" and len(parts) == 3:
        v, w = float(parts[1]), float(parts[2])
        last_drive = time.monotonic()
    elif c == "S":
        v = w = 0.0
        last_drive = None
        log("cmd S (stop)")
    elif c == "Z":
        zero = pitch_now()
        out({"zeroed": True})
        log("cmd Z (zero)")
    else:
        log(f"cmd {line}")


def pitch_now():
    k = (time.monotonic() - t0) % 12.0        # 0-3 flat, 3-3.8 climb, 3.8-7 top, 7-7.5 descend, then flat
    if k < 3:
        return 0.0
    if k < 3.8:
        return 7.1 * (k - 3) / 0.8
    if k < 7:
        return 7.1
    if k < 7.5:
        return 7.1 * (7.5 - k) / 0.5
    return 0.0


out({"hello": "scout-esp32", "fw": "fake-0.1.0", "imu": True})
next_telem = time.monotonic()
drive_count = 0
last_summary = time.monotonic()
while True:
    r, _, _ = select.select([master], [], [], 0.02)
    if r:
        try:
            buf += os.read(master, 256)
        except OSError:
            pass
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.decode("utf-8", "replace").strip()
            if line.startswith("D "):
                drive_count += 1
            handle(line)
    now = time.monotonic()
    if last_drive and now - last_drive > 0.5:
        v = w = 0.0
        last_drive = None
        log("watchdog: no D for 500 ms, motors stopped")
    if now - last_summary >= 1.0:
        if drive_count:
            log(f"drive x{drive_count}  v={v:.2f} w={w:.2f}")
            drive_count = 0
        last_summary = now
    if now >= next_telem:
        next_telem += 0.1
        out({"t": int((now - t0) * 1000), "pitch": round(pitch_now() - zero, 2), "roll": 0.1, "yaw": round((now - t0) * 0.05, 1),
             "v": v, "w": w, "imu": True})
