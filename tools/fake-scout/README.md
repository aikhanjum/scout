# fake-scout

Pretends to be Scout. Serves protocol v2 (`docs/PROTOCOL.md`) on port 8080 from an NDJSON run file, on a loop, and logs every command it receives. Nobody waits for hardware.

## Run

```
cd tools/fake-scout
npm install
npm start                            # plays ../../data/runs/room-scan.ndjson
node server.js path/to/run.ndjson    # any run file (a real one from /runs/latest, or a dashboard recording)
PORT=8081 npm start
```

## Check it

```
curl localhost:8080/status
curl 'localhost:8080/cmd?c=forward'
curl 'localhost:8080/cmd?c=roam'
curl -s localhost:8080/map | head -c 300
curl localhost:8080/runs/latest | head -3
node -e "const w=new WebSocket('ws://localhost:8080/ws');w.onmessage=e=>console.log(e.data)"
```

## The sample run

`npm run gen` rewrites `data/runs/room-scan.ndjson` (deterministic). It is a small 2D simulator, not a script: a 4210 by 5090 mm room with four obstacles, a robot wall-following the perimeter, and a real 360-ray cast against the room's geometry for every frame at 10 Hz. Occlusion shadows behind each obstacle come out for free, which is what makes it a fair test.

The route meets a chair blocking the lane, rounds it, meets a ramp against the far wall, rounds that, squeezes through a 510 mm slot beside a bin, and passes a table that is off the lane — so the table is mapped as geometry but never named, which is exactly what the real robot would do.

Every telemetry frame carries the true pose it was taken from, so this file is also the test fixture for `pi/scout/pose.py`. Then it loops, with `t` and `seq` continuing upward so the dashboard never sees a repeat.

## Notes

- Playback ignores commands. Commands are logged. `drive` is summarised once a second, and the 500 ms teleop watchdog is simulated so you can see whether the dashboard's resend loop is right.
- `/runs/latest` returns the file at full 10 Hz. The real Scout decimates telemetry to 2 Hz and keeps only the newest map frame. Players time frames by `t`, never by rate.
- The fake has no camera, so `/photo/<id>` is always 404 and every obstacle it replays is labelled from the recording, not classified.
