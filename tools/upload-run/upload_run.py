#!/usr/bin/env python3
"""Upload recorded Scout runs to Tiger Data (TimescaleDB), and roll them up into data/history.json.

Laptop tool, run after a run, never from the live loop.

    upload_run.py upload data/runs/e5-corridor.ndjson [more.ndjson] [--simulated] [--replace] [--no-history]
    upload_run.py history [--bucket 1h] [--simulated] [--out data/history.json]
    upload_run.py status

TIGER_URL comes from the environment or the repo-root .env. Without it the feature is off: every
command says so and exits 0. Runs whose fw starts with "fake" are simulated; they are refused unless
--simulated, and stored with simulated = true so nothing shows them unless asked.

Tables (all rows carry run_id and simulated):
  runs    one row per file: space, fw, started_at, ended_at, how the time was known, counts
  events  hypertable: every event frame, real timestamp
  telem   hypertable, columnstore: clearance, pose, mode, empty lidar returns per frame
  scan    hypertable, columnstore: one row per degree per frame (time, deg, range_mm), segmented by run
Continuous aggregates telem_15m, events_15m and scan_15m (15-minute buckets per run) feed the history
rollup and are refreshed after every upload and by a policy every 10 minutes.
Real timestamps are started_at + (t - started_t). Files without started_at (before protocol v2.1)
use the file's modification time as the end of the run, and say so in runs.time_source.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY = ROOT / "data" / "history.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      text PRIMARY KEY,
    space       text NOT NULL,
    fw          text NOT NULL,
    simulated   boolean NOT NULL DEFAULT false,
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz NOT NULL,
    time_source text NOT NULL,            -- 'header' (started_at in the file) or 'mtime' (older file)
    file        text NOT NULL,
    frames      integer NOT NULL,
    events      integer NOT NULL,
    scans       integer NOT NULL,
    config      jsonb,
    uploaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS events (
    time        timestamptz NOT NULL,
    run_id      text NOT NULL,
    space       text NOT NULL,
    simulated   boolean NOT NULL,
    seq         integer NOT NULL,
    kind        text NOT NULL,
    value       double precision,
    unit        text,
    limit_mm    double precision,
    label       text,
    confidence  real,
    x_mm        integer,
    y_mm        integer,
    payload     jsonb
) WITH (tsdb.hypertable, tsdb.partition_column = 'time', tsdb.segmentby = 'run_id', tsdb.orderby = 'time');
CREATE TABLE IF NOT EXISTS telem (
    time          timestamptz NOT NULL,
    run_id        text NOT NULL,
    space         text NOT NULL,
    simulated     boolean NOT NULL,
    t_ms          bigint NOT NULL,
    mode          text,
    lidar         boolean,
    pose          boolean,
    x_mm          integer,
    y_mm          integer,
    heading_deg   real,
    clearance_mm  integer,
    gaps          smallint,
    empty_returns smallint,               -- lidar bins with no return in this frame, of 360
    v             real,
    w             real
) WITH (tsdb.hypertable, tsdb.partition_column = 'time', tsdb.enable_columnstore = true,
        tsdb.segmentby = 'run_id', tsdb.orderby = 'time');
CREATE TABLE IF NOT EXISTS scan (
    time      timestamptz NOT NULL,
    run_id    text NOT NULL,
    simulated boolean NOT NULL,
    deg       smallint NOT NULL,
    range_mm  integer NOT NULL             -- 0 = no return
) WITH (tsdb.hypertable, tsdb.partition_column = 'time', tsdb.enable_columnstore = true,
        tsdb.segmentby = 'run_id', tsdb.orderby = 'time, deg');
CREATE OR REPLACE VIEW runs_real   AS SELECT * FROM runs   WHERE NOT simulated;
CREATE OR REPLACE VIEW events_real AS SELECT * FROM events WHERE NOT simulated;
CREATE OR REPLACE VIEW telem_real  AS SELECT * FROM telem  WHERE NOT simulated;
"""

HISTORY_SQL = """
SELECT a.space, a.bucket, a.runs, a.frames, a.min_clearance, coalesce(b.fails, 0) AS fails, c.empty_share
FROM (
    SELECT space, time_bucket(%(bucket)s::interval, bucket) AS bucket,
           count(DISTINCT run_id)::int AS runs, sum(frames)::bigint AS frames, min(min_clearance)::int AS min_clearance
    FROM telem_15m WHERE NOT simulated OR %(sim)s
    GROUP BY 1, 2
) a
LEFT JOIN (
    SELECT space, time_bucket(%(bucket)s::interval, bucket) AS bucket, sum(fails)::bigint AS fails
    FROM events_15m WHERE NOT simulated OR %(sim)s
    GROUP BY 1, 2
) b ON a.space = b.space AND a.bucket = b.bucket
LEFT JOIN (
    SELECT r.space, time_bucket(%(bucket)s::interval, s.bucket) AS bucket,
           sum(s.empty)::float / nullif(sum(s.samples), 0) AS empty_share
    FROM scan_15m s JOIN runs r USING (run_id) WHERE NOT s.simulated OR %(sim)s
    GROUP BY 1, 2
) c ON a.space = c.space AND a.bucket = c.bucket
ORDER BY a.space, a.bucket
"""

# Continuous aggregates: 15-minute buckets per run, kept up to date by TimescaleDB. The history
# rollup reads these instead of the raw tables, and re-buckets them to whatever --bucket asks for.
CAGG_SQL = [
    """CREATE MATERIALIZED VIEW IF NOT EXISTS telem_15m WITH (timescaledb.continuous) AS
       SELECT time_bucket('15 minutes', time) AS bucket, run_id, space, simulated,
              count(*) AS frames, min(NULLIF(clearance_mm, 0)) AS min_clearance,
              sum(empty_returns) AS empty_returns
       FROM telem GROUP BY 1, 2, 3, 4 WITH NO DATA""",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS events_15m WITH (timescaledb.continuous) AS
       SELECT time_bucket('15 minutes', time) AS bucket, run_id, space, simulated,
              count(*) AS events, sum((kind LIKE '%fail')::int) AS fails
       FROM events GROUP BY 1, 2, 3, 4 WITH NO DATA""",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS scan_15m WITH (timescaledb.continuous) AS
       SELECT time_bucket('15 minutes', time) AS bucket, run_id, simulated,
              count(*) AS samples, sum((range_mm = 0)::int) AS empty
       FROM scan GROUP BY 1, 2, 3 WITH NO DATA""",
]
CAGGS = ("telem_15m", "events_15m", "scan_15m")


def ensure_caggs(conn):
    """Create the continuous aggregates and their refresh policies if missing. Needs autocommit."""
    with conn.cursor() as cur:
        for sql in CAGG_SQL:
            cur.execute(sql)
        for name in CAGGS:
            cur.execute("SELECT add_continuous_aggregate_policy(%s, start_offset => INTERVAL '90 days', "
                        "end_offset => INTERVAL '1 minute', schedule_interval => INTERVAL '10 minutes', if_not_exists => true)", (name,))


def refresh_caggs(conn):
    """Materialise everything now, so a just-uploaded run is in the rollup immediately. Needs autocommit."""
    with conn.cursor() as cur:
        for name in CAGGS:
            cur.execute(f"CALL refresh_continuous_aggregate('{name}', NULL, NULL)")


def bucket_interval(spec):
    """'15m' | '1h' | '1d' -> a Postgres interval string, never finer than the 15-minute aggregates."""
    m = re.match(r"^\s*(\d+)\s*(m|min|minutes?|h|hours?|d|days?)\s*$", spec or "1h")
    if not m:
        raise SystemExit(f"bad --bucket {spec!r}: use 15m, 1h, 1d")
    n, unit = int(m.group(1)), m.group(2)[0]
    minutes = n * {"m": 1, "h": 60, "d": 1440}[unit]
    if minutes < 15:
        print(f"bucket {spec} is finer than the 15-minute aggregates; using 15m")
        n, unit = 15, "m"
    word = dict(m="minute", h="hour", d="day")[unit]
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------- connection, never printed
def tiger_url():
    url = os.environ.get("TIGER_URL", "").strip()
    if not url:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                m = re.match(r"^\s*(?:export\s+)?TIGER_URL\s*=\s*(.*?)\s*$", line)
                if m:
                    url = m.group(1).strip().strip('"').strip("'")
    return url or None


class Redact:
    """Keeps the connection string's user, password and host out of anything we print."""

    def __init__(self, url):
        m = re.search(r"://([^:/@]+)(?::([^@]*))?@([^/:?]+)", url or "")
        self.secrets = [s for s in (m.groups() if m else ()) if s]

    def __call__(self, text):
        for s in self.secrets:
            text = text.replace(s, "***")
        return text


def connect(url):
    import psycopg
    return psycopg.connect(url, connect_timeout=15)


# ---------------------------------------------------------------- reading a run file
def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def read_run(path):
    """Returns (header, frames, run_id, started_at, ended_at, time_source)."""
    raw = path.read_bytes()
    run_id = hashlib.sha256(raw).hexdigest()[:16]
    frames, header = [], None
    for n, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except ValueError:
            print(f"  line {n}: not JSON, skipped")
            continue
        if o.get("type") == "run" and header is None:
            header = o
        else:
            frames.append(o)
    if header is None:
        raise SystemExit(f"{path}: no run header line")
    ts = [f["t"] for f in frames if isinstance(f.get("t"), (int, float))]
    if not ts:
        raise SystemExit(f"{path}: no frames with t")
    started_t = header.get("started_t", ts[0])
    if header.get("started_at"):
        started_at, source = parse_iso(header["started_at"]), "header"
    else:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        started_at, source = mtime - timedelta(milliseconds=max(ts) - started_t), "mtime"
    ended_at = started_at + timedelta(milliseconds=max(ts) - started_t)
    return header, frames, run_id, started_at, started_t, ended_at, source


EVENT_COLS = ("time", "run_id", "space", "simulated", "seq", "kind", "value", "unit", "limit_mm", "label", "confidence", "x_mm", "y_mm", "payload")
TELEM_COLS = ("time", "run_id", "space", "simulated", "t_ms", "mode", "lidar", "pose", "x_mm", "y_mm", "heading_deg", "clearance_mm", "gaps", "empty_returns", "v", "w")
SCAN_COLS = ("time", "run_id", "simulated", "deg", "range_mm")
KNOWN_EVENT_KEYS = {"type", "t", "seq", "kind", "space", "value", "unit", "limit", "label", "confidence", "x_mm", "y_mm"}


def rows_for(frames, run_id, space, simulated, started_at, started_t):
    at = lambda t: started_at + timedelta(milliseconds=t - started_t)
    events, telem, scans = [], [], []
    for f in frames:
        t = f.get("t")
        if not isinstance(t, (int, float)):
            continue
        kind = f.get("type")
        if kind == "event":
            extra = {k: v for k, v in f.items() if k not in KNOWN_EVENT_KEYS}
            events.append((at(t), run_id, space, simulated, f.get("seq", 0), f.get("kind", "?"), f.get("value"), f.get("unit"),
                           f.get("limit"), f.get("label"), f.get("confidence"), f.get("x_mm"), f.get("y_mm"),
                           json.dumps(extra) if extra else None))
        elif kind == "telem":
            scan = f.get("scan") or []
            empty = sum(1 for r in scan if not r)
            telem.append((at(t), run_id, space, simulated, int(t), f.get("mode"), f.get("lidar"), f.get("pose"),
                          f.get("x_mm"), f.get("y_mm"), f.get("heading_deg"), f.get("clearance_mm"),
                          len(f.get("gaps") or []), empty, f.get("v"), f.get("w")))
            when = at(t)
            scans.extend((when, run_id, simulated, deg, int(r or 0)) for deg, r in enumerate(scan))
    return events, telem, scans


# ---------------------------------------------------------------- upload
def copy_rows(cur, table, cols, rows):
    if not rows:
        return
    with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
        for r in rows:
            copy.write_row(r)


def to_columnstore(conn, table):
    """Pack every chunk of a hypertable fully into columnstore now.

    Rows inserted into an already-compressed chunk sit in it poorly packed, and a recompress-in-place
    left the scan table at 7.6 MB where a full repack gives 1.7 MB. So decompress and compress each
    chunk whole: seconds at our sizes, and the number printed afterwards is the real one."""
    with conn.cursor() as cur:
        chunks = [c for (c,) in cur.execute(f"SELECT show_chunks('{table}')")]
        for c in chunks:
            cur.execute("SELECT decompress_chunk(%s::regclass, if_compressed => true)", (c,))
            cur.execute("SELECT compress_chunk(%s::regclass, if_not_compressed => true)", (c,))
    return len(chunks)


def disk_ratio(conn, table):
    """(rows, uncompressed_estimate_bytes, on_disk_bytes): rows times the average tuple size, against
    hypertable_size, which is what the storage bill sees."""
    with conn.cursor() as cur:
        rows = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if not rows:
            return 0, 0, 0
        sample = "TABLESAMPLE SYSTEM (10)" if rows > 200000 else ""
        avg = cur.execute(f"SELECT avg(pg_column_size({table}.*)) FROM {table} {sample}").fetchone()[0]
        disk = cur.execute(f"SELECT hypertable_size('{table}')").fetchone()[0]
        return rows, int(rows * float(avg or 0)), int(disk)


def compression_stats(conn, table):
    with conn.cursor() as cur:
        for fn in ("hypertable_columnstore_stats", "hypertable_compression_stats"):
            try:
                row = cur.execute(f"SELECT before_compression_total_bytes, after_compression_total_bytes, total_chunks, "
                                  f"number_compressed_chunks FROM {fn}(%s)", (table,)).fetchone()
                return row
            except Exception:
                conn.rollback()
    return None


def fmt_bytes(n):
    return "n/a" if n is None else (f"{n / 1e6:.2f} MB" if n >= 1e6 else f"{n / 1e3:.1f} kB")


def cmd_upload(args, url):
    import psycopg
    red = Redact(url)
    t0 = time.time()
    with connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
        uploaded = refused = 0
        for name in args.files:
            path = Path(name)
            header, frames, run_id, started_at, started_t, ended_at, source = read_run(path)
            space, fw = header.get("space") or "(no space)", header.get("fw") or "unknown"
            simulated = fw.startswith("fake")
            print(f"{path.name}: space {space!r}, fw {fw}, {len(frames)} frames, started {started_at:%Y-%m-%d %H:%M:%S}Z ({source}), "
                  f"{(ended_at - started_at).total_seconds():.0f} s")
            if simulated and not args.simulated:
                print("  REFUSED: fw starts with 'fake', this is simulated data. Pass --simulated to upload it tagged as such.")
                refused += 1
                continue
            with conn.cursor() as cur:
                exists = cur.execute("SELECT 1 FROM runs WHERE run_id = %s", (run_id,)).fetchone()
                if exists and not args.replace:
                    print(f"  already uploaded as run {run_id}, skipped (use --replace to redo)")
                    continue
                if exists:
                    for tbl in ("scan", "telem", "events", "runs"):
                        cur.execute(f"DELETE FROM {tbl} WHERE run_id = %s", (run_id,))
                events, telem, scans = rows_for(frames, run_id, space, simulated, started_at, started_t)
                cur.execute("INSERT INTO runs (run_id, space, fw, simulated, started_at, ended_at, time_source, file, frames, events, scans, config) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (run_id, space, fw, simulated, started_at, ended_at, source, path.name, len(telem), len(events),
                             len(scans), json.dumps(header.get("config")) if header.get("config") else None))
                copy_rows(cur, "events", EVENT_COLS, events)
                copy_rows(cur, "telem", TELEM_COLS, telem)
                copy_rows(cur, "scan", SCAN_COLS, scans)
            conn.commit()
            uploaded += 1
            print(f"  run {run_id}{' [SIMULATED]' if simulated else ''}: {len(events)} events, {len(telem)} telem, {len(scans)} scan samples inserted")
    if uploaded:
        # columnstore conversion is a procedure: its own autocommit connection
        with connect(url) as conn:
            conn.autocommit = True
            for table in ("scan", "telem"):
                n = to_columnstore(conn, table)
                rows, est, disk = disk_ratio(conn, table)
                st = compression_stats(conn, table)
                by_stats = f", {st[0] / st[1]:.1f}x by chunk stats" if st and st[0] and st[1] else ""
                print(f"  {table}: {n} chunk(s) in columnstore, {rows:,} rows, {fmt_bytes(est)} uncompressed -> "
                      f"{fmt_bytes(disk)} on disk, compression ratio {est / disk:.1f}x{by_stats}")
            ensure_caggs(conn)
            refresh_caggs(conn)
            print(f"  continuous aggregates refreshed: {', '.join(CAGGS)}")
        print(f"done in {time.time() - t0:.1f} s")
        if not args.no_history:
            cmd_history(args, url)
    else:
        print("nothing uploaded")
    return 2 if refused else 0


# ---------------------------------------------------------------- history
def cmd_history(args, url):
    out = Path(args.out) if getattr(args, "out", None) else DEFAULT_HISTORY
    bucket = bucket_interval(getattr(args, "bucket", None))
    with connect(url) as conn:
        conn.autocommit = True
        ensure_caggs(conn)
        refresh_caggs(conn)
        with conn.cursor() as cur:
            rows = cur.execute(HISTORY_SQL, {"bucket": bucket, "sim": bool(args.simulated)}).fetchall()
    spaces = {}
    for space, start, runs, frames, min_c, fails, empty in rows:
        spaces.setdefault(space, []).append({
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "runs": int(runs), "frames": int(frames), "min_clearance_mm": int(min_c) if min_c is not None else None, "fails": int(fails),
            "empty_share": round(float(empty), 4) if empty is not None else None,
        })
    doc = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
           "bucket": bucket, "includes_simulated": bool(args.simulated),
           "spaces": [{"space": s, "buckets": b} for s, b in sorted(spaces.items())]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"history: {len(rows)} bucket(s) across {len(spaces)} space(s) per {bucket}"
          f"{' (simulated included)' if args.simulated else ''} -> {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


# ---------------------------------------------------------------- status
def cmd_status(args, url):
    with connect(url) as conn, conn.cursor() as cur:
        pg = cur.execute("show server_version").fetchone()[0]
        ts = cur.execute("select extversion from pg_extension where extname = 'timescaledb'").fetchone()
        print(f"postgres {pg}, timescaledb {ts[0] if ts else 'MISSING'}")
        have = {r[0] for r in cur.execute("select table_name from information_schema.tables where table_schema = 'public'")}
        for t in ("runs", "events", "telem", "scan"):
            if t not in have:
                print(f"  {t}: not created yet")
                continue
            n = cur.execute(f"select count(*) from {t}").fetchone()[0]
            extra = ""
            if t in ("telem", "scan") and n:
                rows, est, disk = disk_ratio(conn, t)
                extra = f", {fmt_bytes(est)} uncompressed -> {fmt_bytes(disk)} on disk ({est / disk:.1f}x)"
            print(f"  {t}: {n} rows{extra}")
        caggs = cur.execute("select view_name, materialized_only from timescaledb_information.continuous_aggregates order by 1").fetchall()
        for name, mat_only in caggs:
            n = cur.execute(f"select count(*) from {name}").fetchone()[0]
            print(f"  {name}: continuous aggregate, {n} rows{'' if mat_only else ', real-time'}")
        if "runs" in have:
            for r in cur.execute("select space, started_at, ended_at - started_at, simulated, time_source, file from runs order by started_at"):
                print(f"  run: {r[0]!r} {r[1]:%Y-%m-%d %H:%M}Z {r[2].total_seconds():.0f} s {'SIMULATED' if r[3] else 'real'} time from {r[4]} ({r[5]})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload", help="upload run files, then refresh data/history.json")
    up.add_argument("files", nargs="+")
    up.add_argument("--simulated", action="store_true", help="allow fake runs, tagged simulated")
    up.add_argument("--replace", action="store_true", help="re-upload a run that is already there")
    up.add_argument("--no-history", action="store_true")
    up.add_argument("--bucket", default="1h", help="history bucket: 15m, 1h, 1d")
    up.add_argument("--out", default=None, help="history file (default data/history.json)")
    hi = sub.add_parser("history", help="roll up per space per time bucket into data/history.json")
    hi.add_argument("--bucket", default="1h")
    hi.add_argument("--simulated", action="store_true", help="include simulated runs, and say so in the file")
    hi.add_argument("--out", default=None)
    sub.add_parser("status", help="what is in the database")
    args = ap.parse_args()

    url = tiger_url()
    if not url:
        print("TIGER_URL is not set (environment or repo-root .env): Tiger Data upload is off. Nothing done.")
        return 0
    red = Redact(url)
    try:
        rc = {"upload": cmd_upload, "history": cmd_history, "status": cmd_status}[args.cmd](args, url)
    except Exception as e:  # never let a stack trace print the URL
        print(f"FAILED: {type(e).__name__}: {red(str(e))[:400]}")
        return 1
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
