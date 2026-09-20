// OWNER: tiger agent. Terminal item: live numbers from /live/tiger.json (written by tools/upload-run/live_tail.py).
// Also home of the shared poller (useTiger), the formatters and the FACTS fallback that TigerTab uses.
import { useEffect, useState, type ReactNode } from 'react';
import './tiger.css';

export interface TigerAgg { name: string; real_time: boolean; rows: number }
export interface TigerJson {
  updated_at: string; db_ok: boolean; error: string;
  rows_total: number; rows_session: number; rows_per_s: number;
  on_disk_bytes: number; raw_bytes_est: number; ratio: number;
  last_second: { frames: number; min_clearance_mm: number | null; empty_share: number } | null;
  events_total: number;
  query: { sql: string; ms: number } | null;
  aggregates: TigerAgg[];
  direct_compress: boolean;
  run_id?: string; space?: string; fw?: string; simulated?: boolean;
}

// Measured on the real database with `npm run upload -- status` at 2026-09-20 ~04:00 EDT. Shown only
// when the tailer is not writing tiger.json, and always labelled as not live (CLAUDE.md rule 7).
export const FACTS = {
  measured: '2026-09-20 ~04:00',
  rows_total: 2_089_080,
  raw_bytes_est: 117_000_000, on_disk_bytes: 4_160_000, ratio: 28.1, saved_pct: 96,
  count_ms: 127,
  runs: 4, runs_real: 3, runs_simulated: 1,
  aggregates: ['telem_15m', 'events_15m', 'scan_15m'],
  direct_compress: true,
};

export const fmtInt = (n: number) => Math.round(n).toLocaleString('en-US');
export function fmtBytes(n: number) {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 2)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} kB`;
  return `${n} B`;
}
export const fmtPct = (x: number) => `${Math.round(x * 100)} %`;

const STALE_MS = 10_000;

export interface TigerState { data: TigerJson | null; fetchedAt: number; failed: boolean }

// Polls /live/tiger.json once a second. Keeps the last good document when a poll fails.
export function useTiger(): TigerState {
  const [s, setS] = useState<TigerState>({ data: null, fetchedAt: 0, failed: false });
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch('/live/tiger.json', { cache: 'no-store' });
        if (!r.ok) throw new Error(String(r.status));
        const d = (await r.json()) as TigerJson;
        if (!d || typeof d !== 'object' || typeof d.rows_total !== 'number') throw new Error('not tiger.json');
        if (alive) setS({ data: d, fetchedAt: Date.now(), failed: false });
      } catch {
        if (alive) setS((p) => ({ ...p, failed: true }));
      }
    };
    poll();
    const id = setInterval(poll, 1000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return s;
}

// A clock that re-renders the caller every `ms`, for the climbing counter and the "0.4 s ago".
export function useTick(ms: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(id); }, [ms]);
  return now;
}

export interface Derived { ageMs: number; stale: boolean; rows: number; ageText: string }

// Staleness and the extrapolated row count, from the polled state and the current time.
export function derive(t: TigerState, now: number): Derived {
  const d = t.data;
  if (!d) return { ageMs: Infinity, stale: true, rows: 0, ageText: '' };
  const u = Date.parse(d.updated_at);
  const ageMs = Math.max(0, Number.isFinite(u) ? now - u : now - t.fetchedAt);
  const stale = t.failed || ageMs > STALE_MS;
  const rows = stale ? d.rows_total : d.rows_total + (d.rows_per_s * Math.max(0, now - t.fetchedAt)) / 1000;
  const ageText = ageMs >= 3_600_000 ? `${Math.floor(ageMs / 3_600_000)} h` : ageMs >= 60_000 ? `${Math.floor(ageMs / 60_000)} min` : `${Math.floor(ageMs / 1000)} s`;
  return { ageMs, stale, rows: Math.floor(rows), ageText };
}

// The tags every Tiger screen shows top right: LIVE, RECORDED, NO TAILER, SIMULATED, DB ERROR.
export function StatusTags({ t, now }: { t: TigerState; now: number }) {
  const d = t.data;
  if (!d) return <span className="tg none">NO TAILER</span>;
  const { stale, ageMs, ageText } = derive(t, now);
  return (
    <>
      {d.simulated && <span className="tg sim">SIMULATED</span>}
      {!d.db_ok && <span className="tg err">DB ERROR</span>}
      {stale ? <span className="tg rec">RECORDED {ageText} ago</span> : <><span className="tg live">LIVE</span><span className="dim">{(ageMs / 1000).toFixed(1)} s ago</span></>}
    </>
  );
}

// A one-line description of the timed query in tiger.json.
export function queryText(q: TigerJson['query']) {
  if (!q) return null;
  const s = q.sql.replace(/\s+/g, ' ').trim();
  const desc = /count\(\*\)/i.test(s) && !/where/i.test(s) ? 'count(*) over every row' : s.length > 40 ? `${s.slice(0, 39)}…` : s;
  return `${desc}: ${q.ms} ms`;
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return <div className="row"><span className="k">{k}</span><span className="v">{children}</span></div>;
}

export function TigerPanel() {
  const t = useTiger();
  const now = useTick(100);
  const d = t.data;
  const { rows } = derive(t, now);
  return (
    <div className="tiger tp">
      <div className="row head"><span className="t">Tiger Data</span><span className="r"><StatusTags t={t} now={now} /></span></div>
      {d ? (
        <>
          <Row k="rows"><span className="big">{fmtInt(rows)}</span><span className="sep">·</span><span className="dim">+{fmtInt(d.rows_per_s)}/s</span><span className="sep">·</span>session {fmtInt(d.rows_session)}</Row>
          <Row k="on disk"><span className="big">{fmtBytes(d.on_disk_bytes)}</span><span className="sep">·</span><span className="dim">raw ~{fmtBytes(d.raw_bytes_est)}</span><span className="sep">·</span><span className="green">{d.ratio.toFixed(1)}x smaller</span></Row>
          <Row k="last 1 s">
            {d.last_second
              ? <>{d.last_second.frames} frames<span className="sep">·</span>min clearance {d.last_second.min_clearance_mm == null ? '--' : `${fmtInt(d.last_second.min_clearance_mm)} mm`}<span className="sep">·</span>empty {fmtPct(d.last_second.empty_share)}</>
              : <span className="dim">no frames</span>}
          </Row>
          <Row k="query">{queryText(d.query) ?? <span className="dim">--</span>}</Row>
          <Row k="events">{fmtInt(d.events_total)}<span className="sep">·</span><span className="k">aggs</span> {d.aggregates.length ? d.aggregates.map((a, i) => <span key={a.name}>{i > 0 && <span className="sep">·</span>}{a.name} <span className={a.real_time ? 'green' : 'dim'}>{a.real_time ? 'rt' : 'mat'}</span></span>) : <span className="dim">none</span>}</Row>
          {d.db_ok
            ? <Row k="copy">direct-compress <span className={d.direct_compress ? 'green' : 'dim'}>{d.direct_compress ? 'ON' : 'OFF'}</span></Row>
            : <Row k="error"><span className="red">{d.error || 'database unreachable'}</span></Row>}
        </>
      ) : (
        <>
          <Row k="rows"><span className="big">{fmtInt(FACTS.rows_total)}</span><span className="sep">·</span><span className="dim">count(*) over every row: {FACTS.count_ms} ms</span></Row>
          <Row k="on disk"><span className="big">{fmtBytes(FACTS.on_disk_bytes)}</span><span className="sep">·</span><span className="dim">raw ~{fmtBytes(FACTS.raw_bytes_est)}</span><span className="sep">·</span><span className="green">{FACTS.ratio}x smaller, {FACTS.saved_pct} % saved</span></Row>
          <Row k="runs">{FACTS.runs}<span className="sep">·</span>{FACTS.runs_real} real, {FACTS.runs_simulated} <span className="amber">simulated</span></Row>
          <Row k="aggs">{FACTS.aggregates.join(' · ')}<span className="sep">·</span><span className="dim">15-minute continuous</span></Row>
          <Row k="copy">direct-compress <span className="dim">(tech preview) in the tailer</span></Row>
          <Row k="note"><span className="dim">measured earlier tonight, {FACTS.measured}; not live</span></Row>
        </>
      )}
    </div>
  );
}
