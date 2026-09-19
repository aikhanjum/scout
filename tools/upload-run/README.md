# upload-run

Laptop tool: pushes recorded runs to Tiger Data (TimescaleDB) after the fact, and rolls them up into `data/history.json` for the dashboard's "Room over time" panel. Never part of the live loop.

## Setup (once)

```
npm run setup:tiger                     # or: cd tools/upload-run && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Put `TIGER_URL=postgres://...` in the repo-root `.env` (gitignored). Without it every command prints "Tiger Data upload is off" and exits 0. The tool never prints the URL, user or password, not even in errors.

## Use

```
npm run upload -- upload data/runs/e5-corridor.ndjson         # upload, convert to columnstore, print the compression ratio, refresh history
npm run upload -- upload data/runs/room-scan.ndjson --simulated   # fake runs are refused without this flag; stored tagged simulated
npm run upload -- history --bucket 15m                         # roll up again (simulated runs excluded unless --simulated)
npm run upload -- status                                       # tables, row counts, columnstore sizes, runs
```

Re-uploading the same file is skipped (the run id is the file's hash); `--replace` redoes it.

## What goes where

| Table | Rows | Notes |
| --- | --- | --- |
| `runs` | one per file | space, fw, `started_at`, `ended_at`, `time_source` (`header` or `mtime`), counts, config |
| `events` | one per event frame | hypertable; kind, value, limit, label, confidence, position, extra fields as `payload` |
| `telem` | one per telemetry frame | hypertable in columnstore; clearance, pose, mode, `empty_returns` (lidar bins with no return) |
| `scan` | one per degree per frame | hypertable in columnstore, segmented by run; `deg`, `range_mm` (0 = no return) |

Views `runs_real`, `events_real`, `telem_real` hide simulated rows. Real time = `started_at + (t - started_t)` from the run header (protocol v2.1); older files fall back to the file's modification time as the end of the run.
