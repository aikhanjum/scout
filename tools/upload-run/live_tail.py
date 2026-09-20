#!/usr/bin/env python3
"""Tail a live Scout into Tiger Data one second at a time, and serve what the terminal shows.

    live_tail.py --ws ws://localhost:8080/ws --status http://localhost:8080/status --space "E7 6th floor" \
                 --out data/live/tiger.json --serve 127.0.0.1:8787 [--simulated]

Every telem frame on /ws becomes one telem row and 360 scan rows, every event an events row, all
timed by the wall clock at receipt (UTC). Once a second everything buffered goes in with one COPY
per table (direct-to-columnstore when the server allows it), then a handful of stats queries are
timed and written to --out for the dashboard. A second connection answers GET/POST /tiger/query
inside a read-only transaction so a judge's query never stalls the flush and never writes.

TIGER_URL comes from the environment or the repo-root .env and is never printed. A robot whose fw
starts with "fake" is allowed (the team demoes with it if the robot is down) but every row it
produces is tagged simulated, like --simulated does for anything else.
"""
import argparse
import asyncio
import hashlib
import json
import os
import signal
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upload_run import (CAGGS, EVENT_COLS, ROOT, SCAN_COLS, SCHEMA, TELEM_COLS, Redact, connect, copy_rows,  # noqa: E402
                        ensure_caggs, fmt_bytes, rows_for, tiger_url)

RAW_BYTES_PER_ROW = 56   # 117 MB uncompressed / 2,089,080 scan rows, measured with disk_ratio() on 2026-09-20
FLUSH_EVERY = 1.0        # seconds between flushes, and between stats rounds
SLOW_MS = 500            # a stats query slower than this runs every fifth round instead
BUFFER_FRAMES = 600      # 60 s of telemetry kept while the database is away, then the oldest go
ROW_CAP = 200            # rows a /tiger/query answer may carry
QUERY_TIMEOUT = "3000ms"

# One-second real-time aggregates for the live panel (the 15-minute ones in upload_run.py feed history).
CAGG_1S = {
    "telem_1s": """CREATE MATERIALIZED VIEW telem_1s WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT time_bucket('1 second', time) AS bucket, run_id, simulated, count(*) AS frames,
               min(clearance_mm) FILTER (WHERE clearance_mm > 0) AS min_clearance_mm,
               avg(empty_returns) / 360.0 AS empty_share
        FROM telem GROUP BY 1, 2, 3 WITH NO DATA""",
    "scan_1s": """CREATE MATERIALIZED VIEW scan_1s WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT time_bucket('1 second', time) AS bucket, run_id, simulated, count(*) AS beams,
               count(*) FILTER (WHERE range_mm = 0) AS empty,
               min(range_mm) FILTER (WHERE range_mm > 0) AS nearest_mm,
               avg(range_mm) FILTER (WHERE range_mm > 0) AS mean_mm
        FROM scan GROUP BY 1, 2, 3 WITH NO DATA""",
}

PRESETS = {
    "rows": "SELECT 'runs' AS \"table\", count(*) AS rows FROM runs UNION ALL SELECT 'events', count(*) FROM events "
            "UNION ALL SELECT 'telem', count(*) FROM telem UNION ALL SELECT 'scan', count(*) FROM scan",
    "compression": "SELECT total_chunks, number_compressed_chunks AS compressed_chunks, "
                   "before_compression_total_bytes AS before_bytes, after_compression_total_bytes AS after_bytes, "
                   "round(before_compression_total_bytes::numeric / nullif(after_compression_total_bytes, 0), 1) AS ratio "
                   "FROM {stats_fn}('scan')",
    "door_history": "SELECT bucket, space, min_clearance FROM telem_15m WHERE NOT simulated ORDER BY bucket",   # min_clearance is mm
    "aggregates": "SELECT view_name, materialized_only, compression_enabled FROM timescaledb_information.continuous_aggregates ORDER BY 1",
    "latest_verdicts": "SELECT time, space, kind, value, limit_mm FROM events "
                       "WHERE kind IN ('width_pass', 'width_fail') AND NOT simulated ORDER BY time DESC LIMIT 20",
}


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def jsonable(v):
    """Whatever Postgres hands back, as something json.dumps takes."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date, dtime)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return v.total_seconds()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def write_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, default=jsonable) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- database setup
def setup_db(conn):
    """Schema, the 15-minute aggregates flipped to real time, the 1-second ones created. Needs autocommit."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    ensure_caggs(conn)
    with conn.cursor() as cur:
        for name in CAGGS:
            cur.execute(f"ALTER MATERIALIZED VIEW {name} SET (timescaledb.materialized_only = false)")
        have = {r[0] for r in cur.execute("SELECT view_name FROM timescaledb_information.continuous_aggregates")}
        for name, sql in CAGG_1S.items():
            if name not in have:
                cur.execute(sql)
                cur.execute(f"CALL refresh_continuous_aggregate('{name}', NULL, NULL)")   # history once; the policy takes over
            cur.execute("SELECT add_continuous_aggregate_policy(%s, start_offset => INTERVAL '10 minutes', "
                        "end_offset => INTERVAL '1 second', schedule_interval => INTERVAL '10 seconds', if_not_exists => true)", (name,))


class Session:
    """One tailer start = one run. The id is the start time and the address, so restarts never collide."""

    def __init__(self, args, status):
        self.started_at = utcnow()
        self.ws = args.ws
        self.run_id = hashlib.sha256((iso(self.started_at) + args.ws).encode()).hexdigest()[:16]
        self.fw = str(status.get("fw") or "unknown")
        self.space = args.space or (status.get("run") or {}).get("space") or "(no space)"
        self.simulated = self.fw.startswith("fake") or bool(args.simulated)
        self.config = status.get("config")


# ---------------------------------------------------------------- the writer: buffer, flush, stats
class Tail(threading.Thread):
    def __init__(self, url, session, out):
        super().__init__(name="tail", daemon=True)
        self.url, self.s, self.out, self.red = url, session, out, Redact(url)
        self.conn = None
        self.buf, self.lock, self.frames_buffered, self.dropped = deque(), threading.Lock(), 0, 0
        self.stop, self.ws_ok = threading.Event(), False
        self.direct_compress = True
        self.n_scan = self.n_telem = self.n_events = 0
        self.live = {}                       # the last document written, also served at /tiger/live
        self.round, self.slow, self.cache = 0, {}, {}
        self.flush_ms = self.rows_last = 0
        self.last_flush_at = 0.0
        self.stats_fn = "hypertable_columnstore_stats"   # main() checks which one this server has

    # -- called from the websocket task
    def push(self, frame):
        now = utcnow()
        events, telem, scans = rows_for([frame], self.s.run_id, self.s.space, self.s.simulated, now, frame.get("t"))
        if not (events or telem):
            return
        with self.lock:
            self.buf.append((events, telem, scans))
            self.frames_buffered += bool(telem)
            while self.frames_buffered > BUFFER_FRAMES and self.buf:
                e, t, sc = self.buf.popleft()
                self.frames_buffered -= bool(t)
                self.dropped += len(e) + len(t) + len(sc)

    def buffered(self):
        with self.lock:
            return sum(len(e) + len(t) + len(sc) for e, t, sc in self.buf)

    # -- connection
    def open(self):
        self.close()
        self.conn = connect(self.url)
        self.conn.autocommit = True          # every flush is an explicit transaction; stats run outside one

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None

    def start_run(self):
        """Schema and aggregates, then the runs row. Called once, before the thread starts."""
        self.open()
        setup_db(self.conn)
        s = self.s
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO runs (run_id, space, fw, simulated, started_at, ended_at, time_source, file, frames, events, scans, config) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 'live', %s, 0, 0, 0, %s)",
                        (s.run_id, s.space, s.fw, s.simulated, s.started_at, s.started_at, s.ws, json.dumps(s.config) if s.config else None))

    # -- flush: everything buffered, one transaction, timed
    def flush(self):
        import psycopg
        with self.lock:
            batch = list(self.buf)
        events = [r for e, _, _ in batch for r in e]
        telem = [r for _, t, _ in batch for r in t]
        scans = [r for _, _, sc in batch for r in sc]
        if not batch:
            self.rows_last = self.flush_ms = 0
            return
        t0 = time.time()
        for direct in ((True, False) if self.direct_compress else (False,)):
            try:
                with self.conn.transaction(), self.conn.cursor() as cur:
                    copy_rows(cur, "events", EVENT_COLS, events)     # no columnstore on events: before the GUC
                    if direct:
                        cur.execute("SET LOCAL timescaledb.enable_direct_compress_copy = on")
                    copy_rows(cur, "scan", SCAN_COLS, scans)
                    copy_rows(cur, "telem", TELEM_COLS, telem)
                    cur.execute("UPDATE runs SET ended_at = now(), frames = frames + %s, events = events + %s, scans = scans + %s WHERE run_id = %s",
                                (len(telem), len(events), len(scans), self.s.run_id))
                break
            except psycopg.Error as e:
                # a refused GUC or a COPY the server will not compress directly is a statement error on a live
                # connection: go plain from now on. Anything else is the connection, and the caller reconnects.
                if direct and not self.conn.closed and not isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError)):
                    self.direct_compress = False
                    print(f"direct compress COPY refused: {self.red(str(e)).strip()[:160]}; plain COPY from now on")
                    continue
                raise
        self.flush_ms = round((time.time() - t0) * 1000)
        with self.lock:
            for _ in batch:
                self.buf.popleft()
            self.frames_buffered -= sum(bool(t) for _, t, _ in batch)
        self.n_scan += len(scans); self.n_telem += len(telem); self.n_events += len(events)
        # rows per second of wall clock since the previous flush landed, so a slow link (a 4 s COPY over
        # a phone hotspot carries 4 s of frames) still reads as the true average rate, not a burst
        now = time.time()
        self.rows_last = round((len(scans) + len(telem) + len(events)) / max(now - (self.last_flush_at or t0), 1.0))
        self.last_flush_at = now

    # -- stats: each query timed; one that took over SLOW_MS runs every fifth round and its value is reused between
    def q(self, name, sql, params=None):
        if self.round < self.slow.get(name, 0) and name in self.cache:
            return self.cache[name]
        t0 = time.time()
        with self.conn.cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        ms = round((time.time() - t0) * 1000)
        if ms > SLOW_MS:
            self.slow[name] = self.round + 5
        self.cache[name] = (rows, ms)
        return rows, ms

    def stats(self):
        s = self.s
        count_sql = "SELECT count(*) FROM scan"
        rows, count_ms = self.q("rows_total", count_sql)
        rows_total = rows[0][0]
        disk = self.q("disk", "SELECT hypertable_size('scan')")[0][0][0]
        events_total = self.q("events_total", "SELECT count(*) FROM events")[0][0][0]
        last = self.q("last_second", "SELECT frames, min_clearance_mm, empty_share FROM telem_1s WHERE run_id = %s "
                                     "ORDER BY bucket DESC LIMIT 1", (s.run_id,))[0]
        caggs = self.q("caggs", "SELECT view_name, materialized_only, materialization_hypertable_schema, "
                                "materialization_hypertable_name FROM timescaledb_information.continuous_aggregates ORDER BY 1")[0]
        counts = {}
        if caggs:   # materialised rows, counted on the materialisation tables so this never aggregates raw data
            counts = dict(self.q("cagg_rows", " UNION ALL ".join(f"SELECT '{v}', count(*) FROM {sch}.{tbl}" for v, _, sch, tbl in caggs))[0])
        raw = int(rows_total) * RAW_BYTES_PER_ROW
        return {
            "rows_total": int(rows_total), "on_disk_bytes": int(disk), "raw_bytes_est": raw,
            "ratio": round(raw / disk, 1) if disk else None,
            "last_second": {"frames": int(last[0][0]), "min_clearance_mm": last[0][1], "empty_share": round(float(last[0][2]), 3) if last[0][2] is not None else None}
                           if last else None,
            "events_total": int(events_total), "query": {"sql": count_sql, "ms": count_ms},
            "aggregates": [{"name": v, "real_time": not mat_only, "rows": int(counts.get(v, 0))} for v, mat_only, _, _ in caggs],
        }

    def document(self, stats, error):
        s = self.s
        return {"updated_at": iso(utcnow()), "db_ok": not error, "error": error, "run_id": s.run_id, "space": s.space, "fw": s.fw,
                "simulated": s.simulated, "ws_ok": self.ws_ok, "rows_session": self.n_scan + self.n_telem + self.n_events,
                "rows_per_s": self.rows_last, "flush_ms": self.flush_ms, "buffered": self.buffered(), "dropped": self.dropped,
                "direct_compress": self.direct_compress, **stats}

    def run(self):
        next_at = time.time() + FLUSH_EVERY
        while True:
            error = ""
            try:
                if self.conn is None or self.conn.closed:
                    self.open()
                self.flush()
                stats = self.stats()
            except Exception as e:   # the rows stay buffered; next round reconnects
                error = self.red(f"{type(e).__name__}: {e}").strip()[:300]
                print(f"db: {error}")
                stats = {k: v for k, v in self.live.items() if k in ("rows_total", "on_disk_bytes", "raw_bytes_est", "ratio", "last_second",
                                                                       "events_total", "query", "aggregates")}
                self.close()
            self.live = self.document(stats, error)
            try:
                write_json(self.out, self.live)
            except OSError as e:
                print(f"cannot write {self.out}: {e}")
            if self.round % 10 == 0:
                print(f"{self.live['updated_at']} rows {self.live.get('rows_total', '?'):,} (+{self.rows_last}/s, flush {self.flush_ms} ms, "
                      f"count {self.live.get('query', {}).get('ms', '?')} ms) ratio {self.live.get('ratio')}x "
                      f"direct_compress={self.direct_compress} ws={'up' if self.ws_ok else 'down'}{' ' + error if error else ''}")
            self.round += 1
            if self.stop.is_set():
                break
            time.sleep(max(0.0, next_at - time.time()))
            next_at = max(next_at + FLUSH_EVERY, time.time())
        self.finish()

    def finish(self):
        """Flush what arrived after the last round, close the run, pack loose chunks, print the session."""
        try:
            if self.conn is None or self.conn.closed:
                self.open()
            if self.buffered():
                self.flush()
            with self.conn.cursor() as cur:
                if cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'runs' AND column_name = 'ended_at'").fetchone():
                    cur.execute("UPDATE runs SET ended_at = now() WHERE run_id = %s", (self.s.run_id,))
                loose = cur.execute("SELECT format('%I.%I', chunk_schema, chunk_name), hypertable_name FROM timescaledb_information.chunks "
                                    "WHERE hypertable_name IN ('scan', 'telem', 'events') AND NOT is_compressed").fetchall()
                if not self.direct_compress:   # plain COPY leaves rows loose inside compressed chunks: repack those too
                    loose += cur.execute("SELECT format('%I.%I', chunk_schema, chunk_name), hypertable_name FROM timescaledb_information.chunks "
                                         "WHERE hypertable_name IN ('scan', 'telem') AND is_compressed").fetchall()
                for chunk, table in loose:
                    try:
                        cur.execute("SELECT compress_chunk(%s::regclass, if_not_compressed => true)", (chunk,))
                        print(f"compressed {table} chunk {chunk}")
                    except Exception as e:
                        print(f"could not compress {table} chunk {chunk}: {self.red(str(e)).strip()[:160]}")
                rows_total = cur.execute("SELECT count(*) FROM scan").fetchone()[0]
                disk = cur.execute("SELECT hypertable_size('scan')").fetchone()[0]
            raw = rows_total * RAW_BYTES_PER_ROW
            print(f"run {self.s.run_id}{' [SIMULATED]' if self.s.simulated else ''}: {self.n_scan:,} scan, {self.n_telem:,} telem, "
                  f"{self.n_events} events rows this session{f', {self.dropped} dropped' if self.dropped else ''}; "
                  f"scan now {rows_total:,} rows, {fmt_bytes(raw)} raw -> {fmt_bytes(disk)} on disk, {raw / disk:.1f}x")
        except Exception as e:
            print(f"finish: {self.red(f'{type(e).__name__}: {e}').strip()[:300]}; {self.buffered()} rows still buffered")
        self.close()


# ---------------------------------------------------------------- HTTP: /tiger/health, /tiger/live, /tiger/query
def make_app(tail, url):
    from aiohttp import web
    import psycopg
    red, qlock, qconn = Redact(url), threading.Lock(), {"c": None}

    def run_query(sql):
        """One statement, read only, three seconds, 200 rows, always rolled back. Every answer carries the sql that ran."""
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return {"sql": sql, "columns": [], "rows": [], "ms": 0, "error": "empty query"}
        if ";" in sql:
            return {"sql": sql, "columns": [], "rows": [], "ms": 0, "error": "one statement only"}
        t0 = time.time()
        with qlock:
            try:
                if qconn["c"] is None or qconn["c"].closed:
                    qconn["c"] = connect(url)
                    qconn["c"].autocommit = True
                c = qconn["c"]
                with c.cursor() as cur:
                    cur.execute("BEGIN")
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute(f"SET LOCAL statement_timeout = '{QUERY_TIMEOUT}'")
                    rows, truncated = [], False
                    for r in cur.stream(sql):
                        rows.append([jsonable(v) for v in r])
                        if len(rows) >= ROW_CAP:
                            truncated = True
                            break
                    cols = [d.name for d in cur.description] if cur.description else []
                return {"sql": sql, "columns": cols, "rows": rows, "ms": round((time.time() - t0) * 1000), "error": "", "truncated": truncated}
            except psycopg.Error as e:
                return {"sql": sql, "columns": [], "rows": [], "ms": round((time.time() - t0) * 1000), "error": red(str(e)).strip()[:400]}
            finally:
                try:
                    qconn["c"].execute("ROLLBACK")
                except Exception:
                    qconn["c"] = None

    def reply(doc, status=200):
        return web.json_response(doc, status=status, headers={"Access-Control-Allow-Origin": "*"}, dumps=lambda d: json.dumps(d, default=jsonable))

    async def health(_):
        return reply({"ok": True, "db_ok": tail.live.get("db_ok", False), "ws_ok": tail.ws_ok, "updated_at": tail.live.get("updated_at"),
                      "run_id": tail.s.run_id, "simulated": tail.s.simulated})

    async def live(_):
        return reply(tail.live or {"db_ok": False, "error": "no round yet"})

    async def query(req):
        sql, preset = None, req.query.get("preset")
        if req.method == "POST":
            body = await req.text()
            try:
                o = json.loads(body) if body.strip() else {}
                sql, preset = (o.get("sql"), o.get("preset", preset)) if isinstance(o, dict) else (body, preset)
            except ValueError:
                sql = body
        if preset:
            if preset not in PRESETS:
                return reply({"sql": "", "columns": [], "rows": [], "ms": 0, "error": f"unknown preset {preset!r}; one of {', '.join(PRESETS)}"}, 400)
            sql = PRESETS[preset].format(stats_fn=tail.stats_fn)
        if not sql:
            return reply({"sql": "", "columns": [], "rows": [], "ms": 0, "error": "POST {\"sql\": \"...\"} or GET ?preset=name"}, 400)
        return reply(await asyncio.to_thread(run_query, sql))

    async def options(_):
        return web.Response(status=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST",
                                                 "Access-Control-Allow-Headers": "Content-Type"})

    app = web.Application()
    app.router.add_get("/tiger/health", health)
    app.router.add_get("/tiger/live", live)
    app.router.add_get("/tiger/query", query)
    app.router.add_post("/tiger/query", query)
    app.router.add_route("OPTIONS", "/{tail:.*}", options)
    return app


# ---------------------------------------------------------------- the websocket, reconnecting forever
async def ws_loop(url, tail):
    import websockets
    while True:
        try:
            async with websockets.connect(url, open_timeout=5, max_size=4 * 1024 * 1024) as ws:
                print(f"ws: connected to {url}")
                tail.ws_ok = True
                async for msg in ws:
                    try:
                        f = json.loads(msg)
                    except ValueError:
                        continue
                    if isinstance(f, dict) and f.get("type") in ("telem", "event"):
                        tail.push(f)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if tail.ws_ok or tail.round % 10 == 0:
                print(f"ws: {type(e).__name__}: {str(e)[:120]}; retrying every 2 s")
        tail.ws_ok = False
        await asyncio.sleep(2)


async def fetch_status(url):
    import aiohttp
    for attempt in range(5):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s, s.get(url) as r:
                return await r.json(content_type=None)
        except Exception as e:
            print(f"status: {type(e).__name__}: {str(e)[:120]} ({url}), retry {attempt + 1}/5")
            await asyncio.sleep(2)
    print("status: giving up, fw unknown")
    return {}


async def main_async(args, url):
    from aiohttp import web
    red = Redact(url)
    session = Session(args, await fetch_status(args.status))
    print(f"run {session.run_id}: space {session.space!r}, fw {session.fw}{' [SIMULATED]' if session.simulated else ''}")
    out = Path(args.out) if os.path.isabs(args.out) else ROOT / args.out
    tail = Tail(url, session, out)
    try:
        tail.start_run()
        with tail.conn.cursor() as cur:   # which stats function this server has, for the compression preset
            have = {r[0] for r in cur.execute("SELECT proname FROM pg_proc WHERE proname IN ('hypertable_columnstore_stats', 'hypertable_compression_stats')")}
        tail.stats_fn = "hypertable_columnstore_stats" if "hypertable_columnstore_stats" in have else "hypertable_compression_stats"
    except Exception as e:
        print(f"FAILED to start: {red(f'{type(e).__name__}: {e}').strip()[:300]}")
        return 1
    print(f"tiger: schema, real-time telem_15m/events_15m/scan_15m, telem_1s/scan_1s; writing {out}")
    tail.start()

    host, _, port = args.serve.rpartition(":")
    runner = web.AppRunner(make_app(tail, url), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host or "127.0.0.1", int(port)).start()
    print(f"http: http://{host or '127.0.0.1'}:{port}/tiger/health /tiger/live /tiger/query")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(ws_loop(args.ws, tail))
    await stop.wait()
    print("stopping: final flush")
    task.cancel()
    tail.stop.set()
    await asyncio.to_thread(tail.join)
    await runner.cleanup()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ws", default="ws://localhost:8080/ws")
    ap.add_argument("--status", default="http://localhost:8080/status")
    ap.add_argument("--space", default=None, help="run name (default: the robot's current run, or '(no space)')")
    ap.add_argument("--out", default="data/live/tiger.json", help="relative to the repo root")
    ap.add_argument("--serve", default="127.0.0.1:8787")
    ap.add_argument("--simulated", action="store_true", help="tag every row simulated even if the fw is not fake")
    args = ap.parse_args()
    url = tiger_url()
    if not url:
        print("TIGER_URL missing, tail off")
        return 2
    try:
        return asyncio.run(main_async(args, url))
    except Exception as e:   # never let a stack trace print the URL
        print(f"FAILED: {Redact(url)(f'{type(e).__name__}: {e}').strip()[:400]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
