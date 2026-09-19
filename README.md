# Scout

Scout is a small rover that drives itself around a room like a Roomba and draws a 2D accessibility map of it. Its RPLIDAR A1 does the geometry — where the walls are, where obstacles are, and how wide the gaps between them are — and the Pi camera does the naming, saying what each obstacle is and whether it is a ramp. Every gap is checked against the Ontario Building Code's 860 mm clear width, and anything too narrow gets a red light, a beep and a spoken verdict on the spot. A Raspberry Pi 4 is the brain, an ESP32 drives the motors, and a browser dashboard shows the map filling in live. Built at Hack the North 2026.

Scout has no IMU and measures no slope: a ramp meeting a horizontal scan plane looks exactly like a wall. So ramps are **labelled by the camera and never judged**, and clearance width is the only building-code verdict Scout gives.

## Fresh clone to a moving dashboard

Needs Node 20 or newer (`node --version`). Nothing else. No hardware.

```
git clone https://github.com/aikhanjum/scout.git
cd scout
npm run setup      # installs the fake Scout and the dashboard, about a minute
npm run fake       # terminal 1: a fake Scout on http://localhost:8080, playing a simulated room survey on a loop
npm run dash       # terminal 2: the dashboard on http://localhost:5173
```

Open http://localhost:5173 in Chrome. LINK UP turns green and a room draws itself: walls, obstacles with labels, a red marker where a gap is too narrow, and Scout tracking around the edge. Click the page once so spoken verdicts are allowed. WASD drives (the fake's terminal logs every command), space bar is E-STOP.

Port 8080 busy? `PORT=8081 npm run fake`, then type `ws://localhost:8081/ws` in the Live box and press Connect.

## Then

| I want to | Go to |
| --- | --- |
| Understand the plan, milestones, demo | `docs/SPEC.md`, then `CLAUDE.md` for the rules |
| Send or receive any frame or serial line | `docs/PROTOCOL.md`, the frozen contract |
| Flash the ESP32 | `firmware/README.md` |
| Run the brain on the Mac or the Pi | `pi/README.md` (Python; finds the ESP32 and the lidar on USB by itself) |
| Wire the robot | `hardware/PINMAP.md` |
| Add rules, labels, replay files | `data/README.md` |
| See what was cut and why | `docs/LATER.md` |

```
mission-control/   dashboard (Vite + React)     pi/     brain (Python)            firmware/   ESP32 bridge (PlatformIO)
tools/fake-scout/  fake robot + room simulator  data/   rules and recorded runs   docs/       spec, protocol, later
```

Two hardware questions are still open and both block a driving robot: no motor driver is sourced, and nothing regulates the 18650 pack down to a clean 5 V for the Pi. Details and the failure they cause are in `hardware/PINMAP.md`.
