# RUNBOOK

Demo day, one page. Commands are exact. Run them from the repo root unless a step says otherwise.

Ports, memorise these three: **Pi 8080**, **dashboard 5173**, **fake Scout 8080 on the laptop**.

---

## 1. Start the robot

Power the Pi. It starts on its own through systemd. Give it 30 seconds, then from the laptop:

```
curl http://scout.local:8080/status
```

You want `"lidar":true` and `"esp32":true` in `devices`. If `curl` cannot resolve `scout.local`, the Pi is not on the hotspot. Turn the hotspot on first, then reboot the Pi.

First time on a new Pi, or if `systemctl` says there is no `scout` unit, do the install steps in
`pi/README.md` once. After that it starts on every boot.

If the Pi is not running the service:

```
ssh pi@scout.local
sudo systemctl restart scout
journalctl -u scout -f          # leave this open, it says what it found
```

## 2. Start the dashboard

On the laptop:

```
npm run dash                    # http://localhost:5173
```

Open it in Chrome. Click the page once so speech works. Turn the volume up.

## 3. Point it at the robot

Top right, the **Live** box. Type and press **Connect**:

```
ws://scout.local:8080/ws
```

The badge goes **LINK UP** and the gauges move. The URL is remembered, so you only do this once per laptop.

## 4. Switch to replay (the backup)

If the robot will not connect, or it misbehaves on stage:

1. Pick `table-course.ndjson` in the **Replay** dropdown, top right.
2. Click **Play**.

The badge changes to **REPLAY** and the whole run plays: ramp, slope failure, both gates, the lidar view. It loops. Say out loud that it is a recording. To go back to the robot, press **Connect** again.

Replay needs no robot and no fake server. It reads the file straight from the dashboard.

## 5. If the lidar drops

The WIDTH gauge shows **NO LIDAR** and the lidar panel says "no lidar". The rest keeps working.

1. The service re-probes every 3 seconds. Unplug and replug the lidar's USB and wait 5 seconds.
2. Still nothing: `journalctl -u scout -f` and look for `LIDAR NOT FOUND`. It prints every serial port it can see.
3. The motor must be spinning. If it is silent, the lidar has no power, not a software problem.
4. Last resort, force the port. `ls /dev/ttyUSB*` on the Pi to see what is there, then:

```
sudo systemctl edit scout       # an empty override opens; type exactly these two lines
```
```
[Service]
Environment=SCOUT_LIDAR_PORT=/dev/ttyUSB0
```
```
sudo systemctl restart scout
```

The `[Service]` line is required. Without it the override is ignored and nothing changes.

**Never set `SCOUT_LIDAR_PORT=none`.** That switches the lidar off and everything else still looks healthy, which is the most confusing failure there is.

## 6. If the ESP32 drops

Driving stops working and `POST /cmd` replies `esp32 not connected`. Slope events stop. The lidar and width keep working.

1. Unplug and replug its USB, wait 5 seconds.
2. `journalctl -u scout -f` and look for `ESP32 connected`.
3. If it connects but will not drive, the teleop watchdog is doing its job: the dashboard must be the focused window. Click the page, then hold a key.

## 7. If the dashboard shows nothing at all

- **LINK DOWN** and last known values greyed out: the Pi is unreachable. Go to replay (step 4) and carry on talking.
- Blank page: the dev server died. `npm run dash` again.
- Everything looks alive but numbers are frozen: hard reload with Cmd+Shift+R.

## 8. Running on the laptop without the Pi

Three terminals:

```
npm run fake                                         # fake Scout on :8080
npm run dash                                         # dashboard on :5173
cd pi && .venv/bin/python tools/fake_esp32.py        # prints a pty path
```

Then the real Pi service against real USB hardware, **on 8081** because the fake already holds 8080.
Use `/dev/ttysNNN` from the fake ESP32's output, or leave `SCOUT_ESP32_PORT` off entirely to use a
real ESP32 on USB:

```
cd pi
SCOUT_PORT=8081 SCOUT_ESP32_PORT=/dev/ttysNNN .venv/bin/python -m scout   # fake ESP32
SCOUT_PORT=8081 .venv/bin/python -m scout                                 # real ESP32 on USB
```

Point the dashboard at `ws://localhost:8081/ws` for the real lidar, or `ws://localhost:8080/ws` for the fake.

## 9. Checks that need no hardware

```
cd pi && .venv/bin/python tools/check_audit.py       # slope and width logic
npm run gen                                          # regenerate the replay file
```

## 10. Before you present

- Hotspot on, 2.4 GHz. Pi booted. `curl http://scout.local:8080/status` answers with all three devices true.
- Dashboard full screen, clicked once, volume up, pointed at `ws://scout.local:8080/ws`.
- **TABLE MODE ON**, and the orange "Scale course 1:4" badge is visible. Without it every number on screen is wrong by four times.
- A replay armed in the dropdown, and the backup video open in another tab.
- Fresh AAs, power bank full, spares on the table.
