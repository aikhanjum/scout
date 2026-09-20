# RUNBOOK

Demo day, one page. Commands are exact. Run them from the repo root unless a step says otherwise.

Ports, memorise these three: **Pi 8080**, **dashboard 5173**, **fake Scout 8080 on the laptop**.

---

## 0. Build the room

Scout has no odometry and no IMU. Its position comes from fitting the room's rectangle in every
scan, so the room is not scenery — it is the sensor. Three rules, in order of how badly they bite:

1. **Make it oblong, not square.** One side at least 300 mm longer than the other; 500 mm is
   better. In a square room a quarter turn looks exactly like no turn at all, so if Scout turns a
   corner while the fit is momentarily lost it comes back 90° out and every obstacle on the map
   moves with it, silently. An oblong room recovers from the same event exactly. The service
   prints `ROOM IS SQUARE` at startup if you got this wrong — move a wall before you demo.
2. **Close it.** Four walls meeting at the corners with no gaps. A gap lets the beam out into the
   room beyond, and the fit is then built from whatever it found out there rather than from your
   walls. Corners are where this goes wrong: walls pushed apart to make the room bigger open up
   diagonal slots at each corner. Tape or overlap them.
3. **Keep it clear.** Feet, bags and people standing in it are obstacles that hide walls. Furniture
   against a wall is fine: it is an obstacle, and Scout reports it as one.

Check it before you trust it:

```
cd pi && .venv/bin/python tools/check_pose.py       # on a LIVE Scout: walks every gate, says which failed
```

Want `4. is it a rectangle?` above 55%. A closed room reads 95%+; below 55% there is no pose and
therefore no map. `tools/check_room.py` (no hardware needed) shows what various room shapes do.

---

## 1. Start the robot

Power the Pi. It starts on its own through systemd. Give it 30 seconds, then from the laptop:

```
curl http://scout.local:8080/status
```

You want `"lidar":true` and `"motor":true` in `devices` (`"esp32"` is the same flag under its old name). `"camera"` is always `false`: Scout has no camera. If `curl` cannot resolve `scout.local`, the Pi is not on the hotspot. Turn the hotspot on first, then reboot the Pi.

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

Top right, the **Source** dropdown: pick **Live: scout.local**. The map starts drawing itself; the header stays quiet while the link is healthy and says **Link down** when it is not. The choice is remembered, so you only do this once per laptop.

Then a run:

1. Put Scout on open floor. Slam, the default, takes its position from wherever it starts. The rectangle fitter (`SCOUT_POSE=rect`) needs all four walls in view, not nose to a wall, not behind the table: it reads the room off its first clean scan after a start, and a wall hidden at that moment locks in a wrong room for the whole run.
2. Type the space name and press **Start run**. The tag beside the button reads **Finding position…** and then **Position locked**, or the room size under `rect`, which you check against the tape measure. **No position** after 5 s, or a wrong size, means press **Stop run**, move Scout, start again.
3. Drive with the arrow keys or WASD. Scout stops itself for a second beside each new obstacle, ring gold, then pins it and carries on; keep the key held.
4. **Stop run** stops the motors and ends the recording. Nothing downloads by itself. **Download**, greyed out until the run is stopped, then gives the run report as one HTML page with the map in it, the `.ndjson` replay file, and the events, telemetry and map as CSV. The report is what you hand to a judge; the `.ndjson` is what replays.

Reloading the page mid-run is safe: the dashboard sees the run is active and leaves Scout's map and room frame alone.

## 4. Switch to replay (the backup)

If the robot will not connect, or it misbehaves on stage:

Pick `room-scan.ndjson` in the **Source** dropdown, top right, under Replay.

The badge changes to **REPLAY** and the whole run plays: the room drawing itself, a marker landing on each obstacle as Scout comes near it, the 510 mm slot failing. It loops. Say out loud that it is a recording. To go back to the robot, pick **Live: scout.local** again.

The replay is a simulated room, not a recording of this robot. Say that too if anyone asks what they are watching.

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

## 6. If the motor board drops

Driving stops working and `POST /cmd` replies `motor board not connected`. The lidar and width keep working.

1. Unplug and replug its USB, wait 5 seconds. Opening the port resets the board; it needs about
   2 seconds to come back and say `scoutable-motor-v1 ready` before it will accept anything.
2. `journalctl -u scout -f` and look for `MOTOR  connected`.
3. If it connects but will not drive, the teleop watchdog is doing its job: the dashboard must be the focused window. Click the page, then hold a key.

## 7. If the dashboard shows nothing at all

- **LINK DOWN** and last known values greyed out: the Pi is unreachable. Go to replay (step 4) and carry on talking.
- Blank page: the dev server died. `npm run dash` again.
- Everything looks alive but numbers are frozen: hard reload with Cmd+Shift+R.

## 8. Running on the laptop without the Pi

Three terminals:

```
npm run fake                                         # fake Scout on :8080 (a live simulated robot; add a .ndjson path to play a file instead)
npm run dash                                         # dashboard on :5173
cd pi && .venv/bin/python tools/fake_redboard.py     # prints a pty path
```

Then the real Pi service against real USB hardware, **on 8081** because the fake already holds 8080.
Use `/dev/ttysNNN` from the fake board's output, or leave `SCOUT_MOTOR_PORT` off entirely to use
the real board on USB:

```
cd pi
SCOUT_PORT=8081 SCOUT_MOTOR_PORT=/dev/ttysNNN .venv/bin/python -m scout   # fake board
SCOUT_PORT=8081 .venv/bin/python -m scout                                 # real board on USB
```

Point the dashboard at `ws://localhost:8081/ws` for the real lidar, or `ws://localhost:8080/ws` for the fake.

## 9. Checks that need no hardware

```
cd pi && .venv/bin/python tools/check_audit.py       # clearance and gap logic, 17 checks
cd pi && .venv/bin/python tools/check_room.py        # pose in simulated rooms, including square ones
npm run gen                                          # regenerate the replay file
```

## 10. Before you present

- Hotspot on, 2.4 GHz. Pi booted. `curl http://scout.local:8080/status` answers with all three devices true.
- A failed width verdict is **spoken and shown, not beeped or lit** -- the RedBoard has no buzzer
  and no LEDs. Keep the dashboard volume up; it is the only channel the verdict has.
- Dashboard full screen, clicked once, volume up, pointed at `ws://scout.local:8080/ws`.
- The room is clear of feet and bags. People standing in it become obstacles and break the rectangle fit, and when the fit goes so does the map.
- Drive a lap and watch the room close before you start talking. If the map does not close, keep driving: the width verdicts still fire.
- A replay armed in the dropdown, and the backup video open in another tab.
- Fresh cells, power bank full, spares on the table.
