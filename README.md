# Scout

Scout is a small rover that drives itself around a room like a Roomba and draws a 2D accessibility map of it. Its RPLIDAR A2M8 is its only sense: where the walls are, where obstacles are, and how wide the gaps between them are. There is no camera, so an obstacle is placed on the map and never named. Every gap is checked against the Ontario Building Code's 860 mm clear width, and anything too narrow is called out on the spot — shown on the dashboard and spoken aloud. A Raspberry Pi 4 is the brain, a SparkFun RedBoard drives the motors, and a browser dashboard shows the map filling in live. Built at Hack the North 2026.

Scout has no IMU and measures no slope: a ramp meeting a horizontal scan plane looks exactly like a wall, and nothing on Scout tells the two apart. Clearance width is the only building-code verdict Scout gives.

## Fresh clone to a moving dashboard

Needs Node 20 or newer (`node --version`). Nothing else. No hardware.

```
git clone https://github.com/aikhanjum/scout.git
cd scout
npm run setup      # installs the fake Scout and the dashboard, about a minute
npm run fake       # terminal 1: a fake Scout on http://localhost:8080, a simulated robot in a simulated room
npm run dash       # terminal 2: the dashboard on http://localhost:5173
```

Open http://localhost:5173 in Chrome. A room draws itself: walls, a marker on every obstacle Scout comes near, a red marker where a gap is too narrow, and Scout roaming around the edge until it has closed the loop. Click the page once so spoken verdicts are allowed. Arrow keys or WASD take over and drive the fake robot (it reports every obstacle it comes near and measures every gap it passes, just like the real one), space bar is E-STOP. There is no Roam button any more; `curl 'localhost:8080/cmd?c=roam'` sends the fake back to wall-following.

Port 8080 busy? `PORT=8081 npm run fake`, then type `ws://localhost:8081/ws` in the Live box and press Connect.

## Then

| I want to | Go to |
| --- | --- |
| Catch up on where the project is, or pick up the work | `docs/HANDOFF.md` first |
| Understand the plan, milestones, demo | `docs/SPEC.md`, then `CLAUDE.md` for the rules |
| Send or receive any frame or serial line | `docs/PROTOCOL.md`, the frozen contract |
| Flash the motor board | `docs/HANDOFF-REDBOARD.md` §§4 and 9 (`06_serial_drive.ino`, Arduino IDE) |
| Run the brain on the Mac or the Pi | `pi/README.md` (Python; finds the motor board and the lidar on USB by itself) |
| Wire the robot | `hardware/PINMAP.md` |
| Add rules, replay files | `data/README.md` |
| Upload runs to Tiger Data, refresh "Room over time" | `tools/upload-run/README.md` |
| See what was cut and why | `docs/LATER.md` |

```
mission-control/   dashboard (Vite + React)     pi/     brain (Python)            firmware/   ESP32 bridge (unused)
tools/fake-scout/  fake robot + room simulator  data/   rules and recorded runs   docs/       spec, protocol, later
```

Two hardware questions are still open and both block a driving robot: no motor driver is sourced, and nothing regulates the 18650 pack down to a clean 5 V for the Pi. Details and the failure they cause are in `hardware/PINMAP.md`.
