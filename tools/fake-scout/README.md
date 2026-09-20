# fake-scout

Pretends to be Scout. Serves protocol v2 (`docs/PROTOCOL.md`) on port 8080 and logs every command it receives. Nobody waits for hardware.

Two ways to run it:

- **Live** (the default): a simulated robot in a simulated room. It obeys `drive`, `stop`, `mode`, `run`, `mark` and `map clear`, and behaves as the protocol describes: it starts off wall-following, reports each obstacle once as it comes near it, measures every pinch it passes and says whether it fails the 860 mm limit, and goes idle once it has closed a loop of the room. Arrow keys on the dashboard take it over.
- **Playback**: any run file (a real one from `/runs/latest`, or a dashboard recording) on a loop, with its original timing. Commands are logged and ignored.

## Run

```
cd tools/fake-scout
npm install
npm start                            # live simulation
node server.js path/to/run.ndjson    # play a file on a loop instead
PORT=8081 npm start
```

## Check it

```
curl localhost:8080/status
curl 'localhost:8080/cmd?c=forward'  # the robot drives for 600 ms, then the watchdog stops it
curl 'localhost:8080/cmd?c=roam'
curl -s localhost:8080/map | head -c 300
curl localhost:8080/runs/latest | head -3
node -e "const w=new WebSocket('ws://localhost:8080/ws');w.onmessage=e=>console.log(e.data)"
```

## The room

`sim.js` is the world: a 4210 by 5090 mm room with four obstacles, a 360-ray cast against its geometry for every scan at 10 Hz, and an occupancy grid built from those rays. Occlusion shadows behind each obstacle come out for free, which is what makes it a fair test. The same file drives both modes of the server and the sample run below.

The obstacles: a chair in the wall-following lane, a ramp against the far wall, a bin that leaves a 510 mm slot beside the right wall, and a table that leaves 600 mm beside the left wall. Wall-following round the room reports all four as it comes near them, none with a name (Scout has no camera), fails the bin slot, the ramp-to-bin passage and the table slot -- which is exactly what the real robot would do.

## The sample run

`npm run gen` rewrites `data/runs/room-scan.ndjson` (deterministic): the same room, with the robot following fixed waypoints along the perimeter and the events falling where the route passes each obstacle and pinch. It is the dashboard's replay file and the test fixture for `pi/scout/pose.py`, since every telemetry frame carries the true pose it was taken from.

## Notes

- `drive` is summarised in the log once a second, and the 600 ms teleop watchdog is real: stop resending and the robot stops. So you can see whether the dashboard's resend loop is right.
- While an obstacle within 650 mm is waiting out its one-second confirmation (`measuring: true`, the dashboard's ring turns gold) `drive` is accepted but the motors stay stopped, as on the real robot. Scout moves again the tick the pin lands.
- `/runs/latest` is the run buffer in both modes: every event, telemetry at 2 Hz and the newest map frame, since the last `run start`. Players time frames by `t`, never by rate.
- `map clear` wipes the grid and lets obstacles be reported and gaps measured again. The dashboard sends it on every page load, so a refresh starts the map over.
- `/photo/<id>` is always 404 and `devices.camera` is always `false`, as on the robot.
