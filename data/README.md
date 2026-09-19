# data

Everything the dashboard reads. Served as static files by the dashboard (Vite `publicDir`), so `data/rules.json` is `http://localhost:5173/rules.json`.

- `rules.json`: the clear-width limit with its rule text and source, plus `labels`, the closed set the camera scores a photo against. Adding a label here is all it takes to teach Scout a new word — but rerun `pi/tools/make_clip_labels.py` afterwards, or the Pi will have no vector for it. Format in `docs/PROTOCOL.md` section 8. There is no slope rule: Scout cannot measure slope.
- `runs/*.ndjson`: run logs, for the fake Scout and for replay. `runs/index.json` lists the ones the replay dropdown offers (edit it when you add a run). Recorded runs are gitignored; to ship one as demo insurance, add a `!data/runs/<file>` line to `.gitignore`.
- `runs/room-scan.ndjson` is generated, not recorded: `npm run gen` runs a small 2D simulator (a rectangular room, four obstacles, a real 360-ray cast per frame) and writes a full v2 run. Every telemetry frame carries the true pose it was taken from, which makes it the test fixture for `pi/scout/pose.py` as well as the dashboard's demo data.
