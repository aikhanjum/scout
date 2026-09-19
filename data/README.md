# data

Everything the dashboard reads. Served as static files by the dashboard (Vite `publicDir`), so `data/rules.json` is `http://localhost:5173/rules.json`.

- `rules.json`: limits, rule text, sources, fixes, `table_scale`. Format in `docs/PROTOCOL.md` section 7.
- `spaces.json`: real building spaces, checkpoints and pins. Real full-scale numbers only; table course numbers never go here. Empty until the venue walk. `spaces.example.json` shows the shape.
- `runs/*.ndjson`: run logs, for the fake Scout and for replay. `runs/index.json` lists the ones the replay dropdown offers (edit it when you add a run). Recorded runs are gitignored; to ship one as demo insurance, add a `!data/runs/<file>` line to `.gitignore`.
- `models/*.glb`: Scaniverse exports. Metres, Y up, under 30 MB each. Gitignored except `sample.glb`.
