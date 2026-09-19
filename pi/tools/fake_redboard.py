#!/usr/bin/env python3
"""A fake motor board on a pty, for developing the Pi service without hardware.

    .venv/bin/python tools/fake_redboard.py
    SCOUT_MOTOR_PORT=/dev/ttysNNN .venv/bin/python -m scout      (it prints the exact line)

Speaks the SparkFun RedBoard's dialect from `06_serial_drive.ino`, not the protocol's own lines:
"<L> <R>", "s" and "?" in, a banner and "ok L R" / "err" / "watchdog stop" out. It enforces the
same 600 ms watchdog the real board does, and it applies the same floor of 70 on a non-zero duty,
so a command that would lurch on the bench lurches here too.

It deliberately has no buzzer and no LEDs, because the real board has neither.
"""
import os
import pty
import select
import termios
import time

BANNER = "scoutable-motor-v1 ready"
WATCHDOG_S = 0.6     # the real firmware's timeout
DUTY_FLOOR = 70      # the real firmware raises any non-zero duty below this, the gearboxes hum

master, slave = pty.openpty()
name = os.ttyname(slave)
# A pty echoes by default, so everything written here comes straight back and the script spends its
# life parsing its own replies as commands. A real UART does not do that.
attrs = termios.tcgetattr(slave)
attrs[3] &= ~termios.ECHO
attrs[1] &= ~termios.ONLCR          # and do not rewrite \n as \r\n on the way out
termios.tcsetattr(slave, termios.TCSANOW, attrs)
os.set_blocking(master, False)
print(f"fake RedBoard on {name}")
print(f"run:  SCOUT_MOTOR_PORT={name} .venv/bin/python -m scout", flush=True)

left = right = 0
last_cmd = None
watchdog_logged = False
buf = b""


def out(text):
    try:
        os.write(master, (text + "\n").encode())
    except BlockingIOError:
        pass    # nobody has opened the port yet; drop the line like a real UART would


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def clamp(d):
    d = max(-255, min(255, d))
    if 0 < abs(d) < DUTY_FLOOR:      # what the firmware actually does, not what it was asked
        d = DUTY_FLOOR if d > 0 else -DUTY_FLOOR
    return d


def handle(line):
    global left, right, last_cmd, watchdog_logged
    text = line.strip()
    if not text:
        return
    if text == "s":
        left = right = 0
        last_cmd = None
        log("cmd s (stop)")
        out("ok 0 0")
        return
    if text == "?":
        ms = 0 if last_cmd is None else int((time.monotonic() - last_cmd) * 1000)
        out(f"ok {left} {right}")
        log(f"cmd ? -> L={left} R={right} {ms} ms since last drive")
        return
    parts = text.split()
    if len(parts) == 2:
        try:
            l, r = clamp(int(parts[0])), clamp(int(parts[1]))
        except ValueError:
            log(f"cmd unparseable: {text!r}")
            out("err")
            return
        if (l, r) != (int(parts[0]), int(parts[1])):
            log(f"duty floor applied: asked {parts[0]} {parts[1]}, driving {l} {r}")
        left, right = l, r
        last_cmd = time.monotonic()
        watchdog_logged = False
        out(f"ok {left} {right}")
        return
    log(f"cmd unparseable: {text!r}")
    out("err")


out(BANNER)
log("waiting for commands")
drive_count = 0
seen_command = False
last_banner = last_summary = time.monotonic()

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
            if text and text not in ("s", "?"):
                drive_count += 1
            seen_command = True
            handle(text)

    now = time.monotonic()
    # Opening the real port resets the board over DTR and it prints its banner. A pty cannot be
    # reset, so repeat it until somebody is clearly listening, or the Pi never sees one.
    if not seen_command and now - last_banner >= 1.0:
        last_banner = now
        out(BANNER)
    if last_cmd and now - last_cmd > WATCHDOG_S:
        left = right = 0
        last_cmd = None
        if not watchdog_logged:
            log(f"watchdog: nothing for {int(WATCHDOG_S * 1000)} ms, motors stopped")
            watchdog_logged = True
        out("watchdog stop")
    if now - last_summary >= 1.0:        # drive arrives at 10 Hz, so summarise it once a second
        last_summary = now
        if drive_count:
            log(f"drive x{drive_count}  L={left} R={right}")
            drive_count = 0
