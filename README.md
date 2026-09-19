# Scout

Scout is a small rover that audits indoor spaces for wheelchair accessibility. An IMU measures floor slope and an RPLIDAR A1 measures gap width; both are checked against Ontario Building Code limits (ramps at most 4.76 degrees, doorways at least 860 mm) and every failure gets a red light, a beep and a spoken verdict on the spot. A Raspberry Pi 4 is the brain, an ESP32 drives the motors, and a browser dashboard drives Scout, shows the live readings, and pins each barrier onto a photoreal 3D scan of the space made with an iPhone riding on the robot. Scout audits one space at a time with a person beside it; whole buildings on its own is the roadmap. Built at Hack the North 2026.

## Fresh clone to a moving dashboard

Needs Node 20 or newer (`node --version`). Nothing else. No hardware.

```
git clone https://github.com/aikhanjum/scout.git
cd scout
npm run setup      # installs the fake Scout and the dashboard, about a minute
npm run fake       # terminal 1: a fake Scout on http://localhost:8080, playing a table-course run on a loop
npm run dash       # terminal 2: the dashboard on http://localhost:5173
```

Open http://localhost:5173 in Chrome. LINK UP turns green and the slope gauge, width bar and event feed move on their own. Press TABLE MODE to see the 1:4 course numbers judged against the scaled limit. Click the page once so spoken verdicts are allowed. WASD drives (the fake's terminal logs every command), space bar is E-STOP.

Port 8080 busy? `PORT=8081 npm run fake`, then type `ws://localhost:8081/ws` in the Live box and press Connect.

## Then

| I want to | Go to |
| --- | --- |
| Understand the plan, milestones, demo | `docs/SPEC.md`, then `CLAUDE.md` for the rules |
| Send or receive any frame or serial line | `docs/PROTOCOL.md`, the frozen contract |
| Flash the ESP32 | `firmware/README.md` |
| Run the brain on the Mac or the Pi | `pi/README.md` (Python; finds the ESP32 and the lidar on USB by itself) |
| Wire the robot | `hardware/PINMAP.md` |
| Add real measurements, scans, replay files | `data/README.md` |
| See what was cut and why | `docs/LATER.md` |

```
mission-control/   dashboard (Vite + React)     pi/     brain (Python)            firmware/   ESP32 bridge (PlatformIO)
tools/fake-scout/  fake robot for development   data/   rules, spaces, runs       docs/       spec, protocol, later
```
