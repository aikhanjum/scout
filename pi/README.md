# pi

The Scout brain. Python. Runs on the Pi 4, and unchanged on the Mac with the motor board and the lidar on USB. Speaks `docs/PROTOCOL.md`: sections 1 to 7 to the dashboard on port 8080, section 9 to the motor board over serial.

What it does, in order: read the lidar, fit the room and work out where Scout is, fold the scan into an occupancy map, measure the gap Scout is passing through and judge it, follow the wall, and go around whatever blocks the way, reporting every obstacle it comes near.

## Run

```
cd pi
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # once
.venv/bin/python -m scout
```

Startup log says what it found, loudly:

```
MOTOR  found on /dev/ttyUSB0 (scoutable-motor-v1 ready)
LIDAR  found on /dev/ttyUSB1 (model 24 fw 1.29)
serving http://172.20.10.4:8080  ws://172.20.10.4:8080/ws
```

Devices are found by probing every USB serial port for what answers (the motor board's boot banner, the lidar's device-info reply), never by `/dev/ttyUSB0`, because the two swap names between boots and, on the Pi, both arrive as `/dev/ttyUSB*` with vendor IDs that do not reliably tell them apart. That is why Scout needs no udev rule. A missing device disables only its own feature: no motor board means no driving (`drive` replies `motor board not connected`); no lidar means an empty `scan`, no pose, no map, no width events and a refusal to roam. Serial devices can be unplugged and plugged back while running; the service re-probes every 3 s. `/status.devices` and the `lidar` and `pose` flags in telemetry say what is live.

Environment variables (all optional):

| Variable | Meaning |
| --- | --- |
| `SCOUT_PORT` | HTTP/WS port, default 8080 |
| `SCOUT_MOTOR_PORT` | a device path to skip probing, or `none` to disable. `SCOUT_ESP32_PORT` is the old name and still works |
| `SCOUT_LIDAR_PORT` | same for the lidar |
| `SCOUT_LIDAR_OFFSET_DEG` | the lidar angle that points straight ahead (its own 0 by default) |
| `SCOUT_ROBOT_WIDTH_MM` | how wide Scout is, default 260 |
| `SCOUT_WALL_TARGET_MM` | distance wall following holds from the wall, default 300 |
| `SCOUT_CRUISE` | wall-following speed, 0..1, default 0.4 |

## Without hardware

```
.venv/bin/python tools/fake_redboard.py         # prints a pty path and the exact command to run
SCOUT_MOTOR_PORT=/dev/ttysNNN SCOUT_LIDAR_PORT=none .venv/bin/python -m scout
```

The fake board speaks the RedBoard's own dialect, acknowledges each command with `ok L R`, enforces
the same 600 ms watchdog, and applies the same floor of 70 on a non-zero duty, so a command that
would lurch on the bench lurches here too. With no lidar there is no map, which is the point: it exercises the degraded path. Point the dashboard at `ws://localhost:8080/ws`.

To develop the parts that need a lidar without one, replay recorded scans through the real modules. `data/runs/room-scan.ndjson` carries 646 scans, each with the true pose it was taken from:

```python
import json
from scout.pose import Pose
from scout.mapping import Grid
frames = [json.loads(l) for l in open('../data/runs/room-scan.ndjson') if l.strip()]
p, g = Pose(), Grid()
for f in (x for x in frames if x['type'] == 'telem'):
    if p.update(f['scan'], moving=abs(f['v']) > 0.01 or abs(f['w']) > 0.01):
        if not g.ready(): g.clear(p.room)
        g.integrate(f['scan'], p.x, p.y, p.heading)
        print(p.snapshot(), 'truth', f['x_mm'], f['y_mm'], f['heading_deg'])
```

Measured against that fixture: the room comes out within 10 mm of true, position within 6 mm at p95, heading within 0.2 degrees, on 91% of scans; the other 9% report `pose:false` rather than guessing.

## Check it

```
curl localhost:8080/status
curl 'localhost:8080/cmd?c=forward'
curl 'localhost:8080/cmd?c=roam'
curl -X POST localhost:8080/cmd -d '{"cmd":"config","width_limit_mm":860}'
curl -s localhost:8080/map | head -c 300
curl localhost:8080/runs/latest | head -3
```

## On the Pi

1. Raspberry Pi OS, hostname `scout` (`sudo raspi-config` or `hostnamectl set-hostname scout`), so it is `scout.local`. Join the phone hotspot (2.4 GHz, Maximize Compatibility on).
2. `sudo usermod -aG dialout pi`, log out and in. Then `sudo apt remove -y brltty` -- brltty
   claims CH340 devices on sight, and the RedBoard is a CH340, so its port appears and then
   vanishes a second later and the probe finds nothing.
3. `git clone` the repo to `/home/pi/scout`, then the Run steps above.
4. `sudo cp scout.service /etc/systemd/system/ && sudo systemctl enable --now scout`. Logs: `journalctl -u scout -f`.
5. Plug the motor board and the lidar into the Pi's USB ports, in either order. Both are found
   by probing, so no udev rule and no fixed device path is needed.

## Files

- `scout/main.py` startup and device discovery log. `scout/ports.py` probing. `scout/redboard.py` the motor-board serial bridge thread, and the only file that knows the board's dialect. `scout/lidar.py` lidar thread and the 360-entry scan. `scout/server.py` protocol server and the 10 Hz loop. `scout/config.py` settings.
- `scout/pose.py` where Scout is, by fitting the room's rectangle. `scout/mapping.py` the occupancy grid and obstacle clusters. `scout/audit.py` clearance and the pass/fail. `scout/wallfollow.py` the roaming reflex. All four are pure logic with no I/O, and each docstring names the failure mode it is guarding against — read those before changing a constant.
- `scout/rplidar.py` is vendored from `~/dev/lidar-gaps` (edit there first).
