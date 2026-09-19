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
LIDAR  found on /dev/cu.usbserial-0002 (model 24 fw 1.29)
serving http://172.20.10.4:8080  ws://172.20.10.4:8080/ws
```

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
- `scout/rplidar.py` is vendored from `~/dev/lidar-gaps` (edit there first).
