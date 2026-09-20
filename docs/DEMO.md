# Demo day, in order (2026-09-20)

Everything below was rehearsed at 04:50 with the assembled robot on the phone hotspot.

## 1. Network, robot second

1. iPhone: Personal Hotspot on, **Maximize Compatibility ON** (the Pi only sees 2.4 GHz).
2. Laptop joins the hotspot. Stay on it: the phone is the whole network.
3. Power the Pi **after** the hotspot is up. About 60 s later the phone's banner says **2 connections**.
4. Check: `curl http://scout.local:8080/status` → `"lidar": true, "motor": true`.

Range that matters is robot-to-phone. Keep the phone in the driver's pocket; the robot rebooted twice
tonight, once from power (uptime reset to 14 s), once from driving out of range. Either way it is back
in 60–90 s and the terminal, the tailer and the phone view reconnect on their own.

## 2. Laptop, three terminals, repo root

```
SCOUT_HOST=scout.local:8080 npm run dash                   # copies SF Mono, Vite on :5173, proxies /ws /status /cmd /tiger
npm run tail -- --ws ws://scout.local:8080/ws --status http://scout.local:8080/status --space "E7 6th floor"
npm run tunnel                                             # prints the https URL for phones, writes data/live/tunnel.json
```

Chrome, full screen: `http://localhost:5173/?view=terminal&ws=ws://scout.local:8080/ws`
(or pick **Live: scout.local** in SOURCE once; it is remembered).

## 3. Screens

| Key | Screen | Who |
| --- | --- | --- |
| `1` TERM | the terminal: eyes, radar, clearance, status, health, gaps, Tiger, events, session, room over time, drive, phone QR | everyone |
| `2` MAP | Quaden's blueprint, labelled INFERRED POSITION | only if asked about mapping |
| `3` TIGR | the Tiger Data page: SQL console with presets, schema, aggregates, compression, accuracy | the MLH Tiger judge |
| `4` EYES | Scout's eyes full screen | phones get this by default |

Command line: `/` then a code and Enter: `TERM MAP TIGR EYES ROAM TELE STOP MARK HELP`.
Drive: arrows or WASD (keys light), **Space = E-STOP**. ROAM only after a 30 s corridor rehearsal with a
hand on E-STOP; any arrow key cancels it.

## 4. What to say

- The eyes and the radar are raw: one beam per degree, ten turns a second, colour is distance. Nothing inferred.
- Clearance is the only verdict: perpendicular bounds inside ±45°, refused unless it can see through. 878 mm on a
  doorway the tape measure calls 880 (docs/ACCURACY.md).
- Tiger: every beam is a row. Point at the tile: rows climbing at 3,600/s, `count(*)` over millions of rows in
  well under a second, 20–28x smaller on disk (95 %+ saved), continuous aggregates feeding "room over time".
- Position: there is no odometry and no IMU; the MAP tab says inferred because it is. No verdict uses it.

## 5. If something dies

| Symptom | What it is | Do |
| --- | --- | --- |
| LINK DOWN badge, tiles grey | Pi rebooted or out of range | drive it back near the phone; wait 60–90 s |
| Tiger tile says RECORDED n min ago | tailer lost the DB over the hotspot | say "recorded a minute ago"; it catches up by itself |
| PHONE tile says TUNNEL OFF | `npm run tunnel` not running | phones are optional; skip them |
| Terminal empty, "WAITING FOR SCOUT" | wrong source | SOURCE → Live: scout.local |

## 6. After

Ctrl-C the tailer: it flushes, compresses what is loose and prints the session's rows and the on-disk ratio.
