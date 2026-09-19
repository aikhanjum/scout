# fake-scout

Pretends to be Scout. Serves protocol v1 (`docs/PROTOCOL.md`) on port 8080 from an NDJSON run file, on a loop, and logs every command it receives. Nobody waits for hardware.

## Run

```
cd tools/fake-scout
npm install
npm start                            # plays ../../data/runs/table-course.ndjson
node server.js path/to/run.ndjson    # any run file (a real one from /runs/latest, or a dashboard recording)
PORT=8081 npm start
```

## Check it

```
curl localhost:8080/status
curl 'localhost:8080/cmd?c=forward'
curl -X POST localhost:8080/cmd -d '{"cmd":"config","scale":0.25}'
curl localhost:8080/runs/latest | head -3
node -e "const w=new WebSocket('ws://localhost:8080/ws');w.onmessage=e=>console.log(e.data)"
```

## The sample run

`npm run gen` rewrites `data/runs/table-course.ndjson` (deterministic). It scripts one table-course run at 10 Hz, about 15 s:
idle, `run_start`, drive, up a 7.1 degree ramp, stop and measure, `slope_fail`, landing, 190 mm gate, `width_fail`, a human `mark`, 250 mm gate, `width_pass`, stop, `run_stop`. Then it loops, with `t` and `seq` continuing upward so the dashboard never sees a repeat.

## Notes

- Playback ignores commands. Commands are logged. `drive` is summarised once a second, and the 500 ms teleop watchdog is simulated so you can see whether the dashboard's resend loop is right.
- `/runs/latest` returns the file at full 10 Hz. The real Scout decimates telemetry to 2 Hz. Players time frames by `t`, never by rate.
