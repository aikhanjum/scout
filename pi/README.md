# pi

The Scout brain. Python. Runs on the Pi 4, and unchanged on the Mac with the ESP32 and the lidar on USB. Speaks `docs/PROTOCOL.md`: sections 1 to 7 to the dashboard on port 8080, section 9 to the ESP32 over serial.

What it does, in order: read the lidar, fit the room and work out where Scout is, fold the scan into an occupancy map, measure the gap Scout is passing through and judge it, follow the wall, and stop to photograph and name whatever blocks the way.

## Run

```
cd pi
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # once
.venv/bin/python -m scout
```

Startup log says what it found, loudly:

```
ESP32  found on /dev/cu.usbserial-0001 (fw 0.2.0)
LIDAR  found on /dev/cu.usbserial-0002 (model 24 fw 1.29)
CAMERA started (640x480)
CLIP loaded: 9 labels
serving http://172.20.10.4:8080  ws://172.20.10.4:8080/ws
```

Devices are found by probing every USB serial port for what answers (the ESP32's JSON lines, the lidar's device-info reply), never by `/dev/ttyUSB0`, because the two swap names between boots. A missing device disables only its own feature: no ESP32 means no driving (`drive` replies `esp32 not connected`); no lidar means an empty `scan`, no pose, no map, no width events and a refusal to roam; no camera means every obstacle is labelled `unknown`. Serial devices can be unplugged and plugged back while running; the service re-probes every 3 s. `/status.devices` and the `lidar` and `pose` flags in telemetry say what is live.

Environment variables (all optional):

| Variable | Meaning |
| --- | --- |
| `SCOUT_PORT` | HTTP/WS port, default 8080 |
| `SCOUT_ESP32_PORT` | a device path to skip probing, or `none` to disable |
| `SCOUT_LIDAR_PORT` | same for the lidar |
| `SCOUT_LIDAR_OFFSET_DEG` | the lidar angle that points straight ahead (its own 0 by default) |
| `SCOUT_CAMERA` | `none` disables the camera |
| `SCOUT_ROBOT_WIDTH_MM` | how wide Scout is, default 260 |
| `SCOUT_WALL_TARGET_MM` | distance wall following holds from the wall, default 300 |
| `SCOUT_CRUISE` | wall-following speed, 0..1, default 0.4 |

## Without hardware

```
.venv/bin/python tools/fake_esp32.py            # prints a pty path and the exact command to run
SCOUT_ESP32_PORT=/dev/ttysNNN SCOUT_LIDAR_PORT=none .venv/bin/python -m scout
```

The fake ESP32 echoes drive commands at 10 Hz and enforces the 500 ms watchdog like the real board. With no lidar there is no map, which is the point: it exercises the degraded path. Point the dashboard at `ws://localhost:8080/ws`.

To develop the parts that need a lidar without one, replay recorded scans through the real modules. `data/runs/room-scan.ndjson` carries 660 scans, each with the true pose it was taken from:

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

Measured against that fixture: the room comes out within 10 mm of true, position within 6 mm at p95, heading within 0.2 degrees, on 88% of scans; the other 12% report `pose:false` rather than guessing.

## Check it

```
curl localhost:8080/status
curl 'localhost:8080/cmd?c=forward'
curl 'localhost:8080/cmd?c=roam'
curl -X POST localhost:8080/cmd -d '{"cmd":"config","width_limit_mm":860}'
curl -s localhost:8080/map | head -c 300
curl localhost:8080/runs/latest | head -3
```

## Camera labels

Zero-shot CLIP names what Scout stops in front of. Only the image half runs on the Pi; the text half runs once on a laptop and ships as a small table of label vectors, so the robot needs no tokenizer and no torch.

```
pip install open_clip_torch torch onnx numpy         # on a laptop
python pi/tools/make_clip_labels.py                  # writes pi/models/clip_image.onnx and labels.npz
scp pi/models/* pi@scout.local:~/scout/pi/models/
sudo apt install python3-picamera2                   # on the Pi
.venv/bin/pip install onnxruntime numpy Pillow
```

Without those files Scout still maps, still measures widths and still roams; every obstacle is just reported as `unknown`. The label set lives in `data/rules.json`; edit it and rerun the script.

## On the Pi

1. Raspberry Pi OS, hostname `scout` (`sudo raspi-config` or `hostnamectl set-hostname scout`), so it is `scout.local`. Join the phone hotspot (2.4 GHz, Maximize Compatibility on).
2. `sudo usermod -aG dialout pi`, log out and in.
3. `git clone` the repo to `/home/pi/scout`, then the Run steps above.
4. `sudo cp scout.service /etc/systemd/system/ && sudo systemctl enable --now scout`. Logs: `journalctl -u scout -f`.
5. Plug the ESP32 and the lidar into the Pi's USB ports. Both are found automatically.

## Files

- `scout/main.py` startup and device discovery log. `scout/ports.py` probing. `scout/esp32.py` serial bridge thread. `scout/lidar.py` lidar thread and the 360-entry scan. `scout/server.py` protocol server and the 10 Hz loop. `scout/config.py` settings.
- `scout/pose.py` where Scout is, by fitting the room's rectangle. `scout/mapping.py` the occupancy grid and obstacle clusters. `scout/audit.py` clearance and the pass/fail. `scout/wallfollow.py` the roaming reflex. All four are pure logic with no I/O, and each docstring names the failure mode it is guarding against — read those before changing a constant.
- `scout/camera.py` capture and label. `scout/clip.py` the image half of CLIP through onnxruntime.
- `scout/rplidar.py` is vendored from `~/dev/lidar-gaps` (edit there first).
