# pi

The Scout brain. Python. Runs on the Pi 4, and unchanged on the Mac with the ESP32 and the lidar on USB. Speaks `docs/PROTOCOL.md`: sections 1 to 6 to the dashboard on port 8080, section 8 to the ESP32 over serial.

## Run

```
cd pi
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # once
.venv/bin/python -m scout
```

Startup log says what it found, loudly:

```
ESP32  found on /dev/cu.usbserial-0001 (fw 0.1.0)
LIDAR  found on /dev/cu.usbserial-0002 (model 40 fw 1.27)
serving http://172.20.10.4:8080  ws://172.20.10.4:8080/ws
```

**If the dashboard says NO LIDAR, check `SCOUT_LIDAR_PORT` first.** Set to `none` it disables
the lidar entirely and everything else still looks healthy.

Devices are found by probing every USB serial port for what answers (the ESP32's JSON lines, the lidar's device-info reply), never by `/dev/ttyUSB0`, because the two swap names between boots. A missing device disables only its own feature: no ESP32 means no drive and no slope events (`drive` replies `esp32 not connected`); no lidar means empty `sweep`, `width_mm` 0 and no width events. Either can be unplugged and plugged back while running; the service re-probes every 3 s. `/status.devices` and the `imu`/`lidar` flags in telemetry say what is live.

Environment variables (all optional):

| Variable | Meaning |
| --- | --- |
| `SCOUT_PORT` | HTTP/WS port, default 8080 |
| `SCOUT_ESP32_PORT` | a device path to skip probing, or `none` to disable |
| `SCOUT_LIDAR_PORT` | same for the lidar |
| `SCOUT_LIDAR_OFFSET_DEG` | the lidar angle that points straight ahead (its own 0 by default) |
| `SCOUT_WIDTH_OFFSET_MM` | added to left + right; 0 when the lidar sits at the body's centre |

## Without hardware

```
.venv/bin/python tools/fake_esp32.py            # prints a pty path and the exact command to run
SCOUT_ESP32_PORT=/dev/ttysNNN SCOUT_LIDAR_PORT=none .venv/bin/python -m scout
```

The fake climbs a 7.1 degree ramp every 12 s, so `slope_fail` events fire, and it logs drive commands and the watchdog like the real board. Point the dashboard at `ws://localhost:8080/ws`.

## Check it

```
.venv/bin/python tools/check_audit.py     # slope and width logic, 17 checks, no hardware
curl localhost:8080/status
curl 'localhost:8080/cmd?c=forward'
curl -X POST localhost:8080/cmd -d '{"cmd":"config","scale":0.25}'
curl localhost:8080/runs/latest | head -3
```

## On the Pi

1. Raspberry Pi OS, hostname `scout` (`sudo raspi-config` or `hostnamectl set-hostname scout`), so it is `scout.local`. Join the phone hotspot (2.4 GHz, Maximize Compatibility on).
2. `sudo usermod -aG dialout pi`, log out and in.
3. `git clone` the repo to `/home/pi/scout`, then the Run steps above.
4. `sudo cp scout.service /etc/systemd/system/ && sudo systemctl enable --now scout`. Logs: `journalctl -u scout -f`.
5. Plug the ESP32 and the lidar into the Pi's USB ports. Both are found automatically.

## Files

- `scout/main.py` startup and device discovery log. `scout/ports.py` probing. `scout/esp32.py` serial bridge thread. `scout/lidar.py` lidar thread and the five-angle sweep. `scout/audit.py` slope and width logic, pure. `scout/server.py` protocol server and the 10 Hz loop. `scout/config.py` settings.
- `scout/rplidar.py` and `scout/gaps.py` are vendored from `~/dev/lidar-gaps` (edit there first, its tests run in seconds).

## Lidar notes

- **The motor start depends on the model.** An A1 spins whenever DTR is low and has no motor
  command. An A2 or A3 on an accessory board needs `CMD_SET_MOTOR_PWM`; the S, T, C and M series
  take an RPM command. Get it wrong and the device answers device info and health perfectly,
  accepts the scan command, returns a valid scan header, and then streams nothing at all. The
  driver picks the mode from the model id and logs it; `/status` and the startup log show it.
- **Express scan** is used when the device supports it, and doubles the sample rate: on the A2M8
  here, 4000 samples a second and about 320 points a rotation, against 2000 and 160 for the
  standard stream. A device that will not answer `EXPRESS_SCAN` falls back automatically.
- **The `scan` frame** (PROTOCOL.md v1.2) carries one whole rotation plus detected gaps at 2 Hz,
  for the dashboard's lidar view.
- **Gaps can open a width pinch** (PROTOCOL.md v1.3), so a doorway Scout sees ahead is audited
  even if it never drives through it. Only `see_through` gaps count, and only ones roughly ahead
  (`GAP_AHEAD_DEG`) and close by (`GAP_MAX_RANGE_MM`). An `unverified` gap can never become an
  event: an arc of no-returns is an opening or a surface that does not reflect, and one rotation
  cannot tell which. A gap is held for `GAP_LATCH_S` after it was last seen, so a doorway that
  flickers between rotations fires once rather than every couple of seconds.
- **Expect chatter in a cluttered room.** Sitting on a desk at scale 1.0 the gap source fires a
  few width events a minute, because a 230 mm gap between two objects really is under the 860 mm
  limit. On the table course with TABLE MODE on the lane is clean and it should be quiet. If it
  is not, tighten `GAP_AHEAD_DEG` and `GAP_MAX_RANGE_MM` in `scout/audit.py`.
