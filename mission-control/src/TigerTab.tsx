// OWNER: tiger agent. The Tiger Data presentation tab for the MLH judge. Read-only SQL via /tiger/query.
import { memo, useCallback, useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Tabs } from './Tabs';
import { History } from './History';
import { C } from './palette';
import { FACTS, StatusTags, derive, fmtBytes, fmtInt, fmtPct, queryText, useTick, useTiger, type TigerJson, type TigerState } from './TigerPanel';
import './tiger.css';

interface QueryResult { columns: string[]; rows: unknown[][]; ms: number; error: string; sql?: string }

const LIMIT_MM = 860;   // Ontario Building Code 3.8.3.3, data/rules.json
const MAX_ROWS = 200;
const CHART_MAX_MM = 2000;

// The SQL each preset runs, as shown in the console. If the tailer returns its own `sql` with a preset,
// that text replaces this one, so what the judge reads is what ran.
const PRESETS: { name: string; label: string; sql: string }[] = [
  { name: 'rows', label: 'rows', sql: `select 'runs' as "table", count(*) as rows from runs
union all select 'events', count(*) from events
union all select 'telem',  count(*) from telem
union all select 'scan',   count(*) from scan` },
  { name: 'compression', label: 'compression', sql: `select 'scan' as hypertable, total_chunks, number_compressed_chunks,
       before_compression_total_bytes, after_compression_total_bytes
from hypertable_columnstore_stats('scan')
union all
select 'telem', total_chunks, number_compressed_chunks,
       before_compression_total_bytes, after_compression_total_bytes
from hypertable_columnstore_stats('telem')` },
  { name: 'door_history', label: 'door history', sql: `select space, bucket, min(min_clearance) as min_clearance_mm, sum(frames) as frames
from telem_15m
where not simulated
group by 1, 2
order by 2, 1` },
  { name: 'aggregates', label: 'aggregates', sql: `select view_name, materialized_only, compression_enabled
from timescaledb_information.continuous_aggregates
order by 1` },
  { name: 'latest_verdicts', label: 'latest verdicts', sql: `select time, space, kind, value as mm, limit_mm
from events
where kind like 'width_%' and not simulated
order by time desc
limit 20` },
];

// ?tiger=http://localhost:8784 points the console at another origin during development; default same-origin.
function queryBase(): string {
  const q = new URLSearchParams(location.search).get('tiger');
  return q ? q.replace(/\/+$/, '') : '';
}

async function runQuery(base: string, arg: { sql: string } | { preset: string }): Promise<QueryResult> {
  const t0 = performance.now();
  const none = (error: string): QueryResult => ({ columns: [], rows: [], ms: 0, error });
  try {
    const r = 'preset' in arg
      ? await fetch(`${base}/tiger/query?preset=${encodeURIComponent(arg.preset)}`, { cache: 'no-store' })
      : await fetch(`${base}/tiger/query`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ sql: arg.sql }) });
    const text = await r.text();
    let j: Partial<QueryResult>;
    try { j = JSON.parse(text); } catch { return none(r.ok ? 'no query endpoint: live_tail.py is not running' : `HTTP ${r.status}`); }
    if (!j || typeof j !== 'object') return none('bad reply');
    return {
      columns: Array.isArray(j.columns) ? j.columns.map(String) : [],
      rows: Array.isArray(j.rows) ? j.rows : [],
      ms: typeof j.ms === 'number' ? j.ms : Math.round(performance.now() - t0),
      error: typeof j.error === 'string' ? j.error : (r.ok ? '' : `HTTP ${r.status}`),
      sql: typeof j.sql === 'string' ? j.sql : undefined,
    };
  } catch (e) {
    return none(`unreachable: ${e instanceof Error ? e.message : String(e)}`);
  }
}

const isNum = (v: unknown) => typeof v === 'number' || (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v));
const num = (v: unknown) => (typeof v === 'number' ? v : Number(v));

function cellText(v: unknown): string {
  if (v == null) return 'null';
  if (typeof v === 'number') return Number.isInteger(v) ? fmtInt(v) : v.toFixed(Math.abs(v) < 10 ? 3 : 1);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'string') return v;
  return JSON.stringify(v);
}

const when = (v: unknown) => {
  const ms = typeof v === 'string' || typeof v === 'number' ? Date.parse(String(v)) : NaN;
  return Number.isFinite(ms) ? new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) : String(v ?? '');
};

// ---------------------------------------------------------------- reading preset results, column names are the tailer's
const findCol = (cols: string[], ...needles: string[]) => cols.findIndex((c) => needles.some((n) => c.toLowerCase().includes(n)));

const TABLES = ['runs', 'events', 'telem', 'scan'] as const;
function tableCounts(r: QueryResult | undefined): Partial<Record<string, number>> {
  const out: Partial<Record<string, number>> = {};
  if (!r || r.error) return out;
  for (const row of r.rows) {
    const name = row.find((v) => typeof v === 'string' && (TABLES as readonly string[]).includes(v.toLowerCase())) as string | undefined;
    const n = row.find((v) => isNum(v) && v !== name);
    if (name && n != null) out[name.toLowerCase()] = num(n);
  }
  if (!Object.keys(out).length && r.rows.length === 1) r.columns.forEach((c, i) => { if ((TABLES as readonly string[]).includes(c.toLowerCase())) out[c.toLowerCase()] = num(r.rows[0][i]); });
  return out;
}

interface Bar { space: string; when: string; mm: number | null }
function doorBars(r: QueryResult | undefined): Bar[] | null {
  if (!r || r.error || !r.rows.length) return null;
  const si = findCol(r.columns, 'space');
  const ti = findCol(r.columns, 'bucket', 'time', 'start');
  const mi = findCol(r.columns, 'clear', 'min', 'mm');
  if (mi < 0) return null;
  return r.rows.map((row) => ({
    space: si >= 0 ? String(row[si] ?? '') : 'all spaces',
    when: ti >= 0 ? when(row[ti]) : '',
    mm: row[mi] == null || !isNum(row[mi]) ? null : num(row[mi]),
  }));
}

interface Chunks { table: string; total: number | null; compressed: number | null; before: number | null; after: number | null }
function chunkInfo(r: QueryResult | undefined): Chunks[] {
  if (!r || r.error || !r.rows.length) return [];
  const ni = findCol(r.columns, 'hypertable', 'table', 'name');
  const ti = findCol(r.columns, 'total_chunks');
  const ci = findCol(r.columns, 'compressed_chunks');
  const bi = findCol(r.columns, 'before_compression_total', 'before', 'raw');
  const ai = findCol(r.columns, 'after_compression_total', 'after', 'disk');
  const get = (row: unknown[], i: number) => (i >= 0 && isNum(row[i]) ? num(row[i]) : null);
  return r.rows.map((row) => ({
    table: ni >= 0 ? String(row[ni] ?? '') : String(row.find((v) => typeof v === 'string') ?? ''),
    total: get(row, ti), compressed: get(row, ci), before: get(row, bi), after: get(row, ai),
  }));
}

// history.json (docs/PROTOCOL.md section 8): the chart's fallback when the door_history preset is not answered.
interface HistoryDoc { generated_at: string; bucket: string; includes_simulated: boolean; spaces: { space: string; buckets: { start: string; min_clearance_mm: number | null }[] }[] }
const historyBars = (h: HistoryDoc | null): Bar[] | null =>
  h && h.spaces.length ? h.spaces.flatMap((s) => s.buckets.map((b) => ({ space: s.space, when: when(b.start), mm: b.min_clearance_mm }))) : null;

// ---------------------------------------------------------------- pieces
function Sec({ n, title, bullet, children }: { n: number; title: string; bullet?: string; children: ReactNode }) {
  return (
    <section className="sec">
      <div className="sec-head"><span className="n">{n}</span><span className="title">{title}</span>{bullet && <span className="bullet">prize bullet: "{bullet}"</span>}</div>
      <div className="sec-body">{children}</div>
    </section>
  );
}

function ResultTable({ r }: { r: QueryResult }) {
  const shown = r.rows.slice(0, MAX_ROWS);
  const numeric = r.columns.map((_, i) => shown.length > 0 && shown.every((row) => row[i] == null || typeof row[i] === 'number'));
  return (
    <>
      <div className="result">
        <table className="rs">
          <thead><tr>{r.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={i}>{r.columns.map((_, j) => <td key={j} className={row[j] == null ? 'nul' : numeric[j] ? 'num' : ''}>{cellText(row[j])}</td>)}</tr>
            ))}
            {shown.length === 0 && <tr><td className="dim" colSpan={Math.max(1, r.columns.length)}>no rows</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="cfoot dim">
        <span>{fmtInt(r.ms)} ms<span className="sep">·</span>{fmtInt(r.rows.length)} rows{r.rows.length > MAX_ROWS ? ` (showing ${MAX_ROWS})` : ''}<span className="sep">·</span>read-only transaction</span>
      </div>
    </>
  );
}

const Console = memo(function Console({ base }: { base: string }) {
  const [sql, setSql] = useState(PRESETS[0].sql);
  const [active, setActive] = useState<string | null>(PRESETS[0].name);
  const [res, setRes] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState(false);

  const runPreset = useCallback(async (name: string) => {
    const p = PRESETS.find((x) => x.name === name);
    if (!p) return;
    setActive(name); setSql(p.sql); setBusy(true);
    const r = await runQuery(base, { preset: name });
    if (r.sql) setSql(r.sql);
    setRes(r); setBusy(false);
  }, [base]);

  const runText = useCallback(async () => {
    setActive(null); setBusy(true);
    setRes(await runQuery(base, { sql }));
    setBusy(false);
  }, [base, sql]);

  useEffect(() => { runPreset(PRESETS[0].name); }, [runPreset]);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); runText(); }
  };

  return (
    <>
      <div className="presets">
        <span className="k">presets</span>
        {PRESETS.map((p) => <button key={p.name} className={active === p.name ? 'b on' : 'b'} disabled={busy} onClick={() => runPreset(p.name)}>{p.label}</button>)}
      </div>
      <textarea className="sql" spellCheck={false} value={sql} onChange={(e) => { setSql(e.target.value); setActive(null); }} onKeyDown={onKey} />
      <div className="cfoot">
        <button className="b run" disabled={busy} onClick={runText}>run</button>
        <span className="dim">Cmd/Ctrl+Enter</span>
        <span className="dim">read-only session; the credential never leaves the laptop</span>
        {busy && <span className="amber">running…</span>}
      </div>
      {res && res.error && <div className="err" style={{ marginTop: 8 }}>{res.error}</div>}
      {res && !res.error && <ResultTable r={res} />}
    </>
  );
});

function Pipe({ d, rows, live }: { d: TigerJson; rows: number; live: boolean }) {
  const frames = d.last_second?.frames ?? 10;
  const nodes: [string, string, boolean?][] = [
    ['RPLIDAR A2M8', '360 beams per turn'],
    ['lidar frames', `${frames} frames/s × 360 beams`],
    ['/ws', 'protocol v2, 10 Hz telem'],
    ['live_tail.py', `+${fmtInt(d.rows_per_s)} rows/s`],
    ['COPY', `direct compress ${d.direct_compress ? 'ON' : 'OFF'}`],
    ['hypertable scan', `${fmtInt(rows)} rows`],
    ['columnstore', `${d.ratio.toFixed(1)}x smaller`],
    ['continuous aggregates', d.aggregates.length ? d.aggregates.map((a) => a.name).join(', ') : '1 s, 15 min'],
    ['this page', live ? '/tiger/query, read-only' : 'waiting for the tailer', true],
  ];
  return (
    <div className="pipe">
      {nodes.map(([k, v, here], i) => (
        <span key={k} style={{ display: 'contents' }}>
          {i > 0 && <span className="arrow">→</span>}
          <span className={here ? 'node here' : 'node'}><span className="k">{k}</span><span className="v">{v}</span></span>
        </span>
      ))}
    </div>
  );
}

function LiveRows({ t }: { t: TigerState }) {
  const now = useTick(100);
  return <>{fmtInt(derive(t, now).rows)}</>;
}

function DoorChart({ bars, source }: { bars: Bar[]; source: string }) {
  const bySpace = new Map<string, Bar[]>();
  for (const b of bars) bySpace.set(b.space, [...(bySpace.get(b.space) ?? []), b]);
  const limitPct = (LIMIT_MM / CHART_MAX_MM) * 100;
  return (
    <div className="chart">
      <div className="k">min clearance per 15-minute bucket per space, {LIMIT_MM} mm line marked<span className="sep">·</span>{source}</div>
      {[...bySpace.entries()].map(([space, list]) => (
        <div key={space}>
          <div className="space">{space}</div>
          {list.map((b, i) => {
            const pass = b.mm != null && b.mm >= LIMIT_MM;
            const w = b.mm == null ? 0 : Math.min(100, (b.mm / CHART_MAX_MM) * 100);
            return (
              <div key={i} className="brow">
                <span className="dim">{b.when}</span>
                <span className="track"><i className="fill" style={{ width: `${w}%`, background: pass ? C.green : C.red }} /><i className="limit" style={{ left: `${limitPct}%` }} /></span>
                <span className={b.mm == null ? 'dim' : pass ? 'green' : 'red'}>{b.mm == null ? '--' : `${fmtInt(b.mm)} mm ${pass ? 'pass' : 'FAIL'}`}</span>
              </div>
            );
          })}
        </div>
      ))}
      <div className="axis dim"><span /><span className="ax"><span style={{ left: 0 }}>0</span><span style={{ left: `${limitPct}%` }} className="amber">{LIMIT_MM}</span><span style={{ left: '100%' }} className="end">{CHART_MAX_MM} mm</span></span><span /></div>
    </div>
  );
}

// ---------------------------------------------------------------- the page
const factsDoc = (): TigerJson => ({
  updated_at: '', db_ok: true, error: '',
  rows_total: FACTS.rows_total, rows_session: 0, rows_per_s: 0,
  on_disk_bytes: FACTS.on_disk_bytes, raw_bytes_est: FACTS.raw_bytes_est, ratio: FACTS.ratio,
  last_second: null, events_total: 0, query: { sql: 'select count(*) from scan', ms: FACTS.count_ms },
  aggregates: FACTS.aggregates.map((name) => ({ name, real_time: true, rows: 0 })),
  direct_compress: FACTS.direct_compress,
});

export function TigerTab() {
  const t = useTiger();
  const now = useTick(1000);
  const base = useMemo(queryBase, []);
  const [pre, setPre] = useState<Record<string, QueryResult>>({});
  const [hist, setHist] = useState<HistoryDoc | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const out: Record<string, QueryResult> = {};
      await Promise.all(['rows', 'compression', 'door_history'].map(async (p) => { out[p] = await runQuery(base, { preset: p }); }));
      if (alive) setPre(out);
    };
    load();
    const id = setInterval(load, 20_000);
    fetch('/history.json', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null))
      .then((h) => { if (alive && h && Array.isArray(h.spaces)) setHist(h); }).catch(() => {});
    return () => { alive = false; clearInterval(id); };
  }, [base]);

  const live = !!t.data;
  const d = t.data ?? factsDoc();
  const { rows, stale } = derive(t, now);
  const counts = tableCounts(pre.rows);
  const chunks = chunkInfo(pre.compression);
  const queryUp = Boolean(pre.rows && !pre.rows.error);
  const bars = doorBars(pre.door_history) ?? historyBars(hist);
  const barSource = doorBars(pre.door_history) ? 'door_history preset, live' : hist ? `data/history.json, exported ${when(hist.generated_at)}` : '';
  const savedPct = d.ratio > 0 ? (1 - 1 / d.ratio) * 100 : 0;
  const notLive = `measured ${FACTS.measured}, not live`;

  const count = (table: string, fallback?: number) => {
    const v = counts[table];
    if (v != null) return fmtInt(v);
    if (fallback != null) return fmtInt(fallback);
    return '--';
  };

  return (
    <div className="tiger tiger-page">
      <header className="tbar">
        <span className="tbrand">SCOUT</span>
        <Tabs />
        <span className="status">
          {d.run_id && <span>run {d.run_id.slice(0, 8)}<span className="sep">·</span>{d.space}<span className="sep">·</span>fw {d.fw}</span>}
          <StatusTags t={t} now={now} />
        </span>
      </header>
      <div className="wrap">
        <p className="thesis">Every lidar return Scout has seen is a row in one Postgres database, queried with plain SQL while it is still arriving.</p>
        {!live && <div className="banner">NO TAILER: live_tail.py is not writing data/live/tiger.json. Every number below was {notLive}.</div>}
        {live && !d.db_ok && <div className="banner err">DB ERROR: {d.error || 'database unreachable'}</div>}
        {live && d.simulated && <div className="banner">SIMULATED: this run comes from the fake Scout (fw {d.fw}). It is tagged simulated in every table and hidden from the views by default.</div>}
        {live && stale && d.db_ok && <div className="banner">RECORDED: tiger.json stopped updating. The numbers are the last ones written, not live.</div>}

        <Sec n={1} title="The pipe">
          <Pipe d={d} rows={rows} live={live} />
          <div className="note">
            {live ? <><LiveRows t={t} /> rows so far<span className="sep">·</span>{fmtInt(d.rows_session)} this session<span className="sep">·</span>{fmtBytes(d.on_disk_bytes)} on disk<span className="sep">·</span>{d.last_second ? <>last second: {d.last_second.frames} frames, min clearance {d.last_second.min_clearance_mm == null ? '--' : `${fmtInt(d.last_second.min_clearance_mm)} mm`}, {fmtPct(d.last_second.empty_share)} empty beams</> : 'no frames in the last second'}</>
              : <>{fmtInt(FACTS.rows_total)} rows<span className="sep">·</span>{fmtBytes(FACTS.on_disk_bytes)} on disk<span className="sep">·</span>{notLive}</>}
          </div>
        </Sec>

        <Sec n={2} title="SQL console" bullet="standard SQL on big data">
          <Console base={base} />
          {!queryUp && <div className="note amber">/tiger/query is not answering{pre.rows?.error ? ` (${pre.rows.error})` : ''}. Start tools/upload-run/live_tail.py; the timed count(*) over the whole table was {FACTS.count_ms} ms, {notLive}.</div>}
        </Sec>

        <Sec n={3} title="Schema" bullet="unified stack">
          <div className="schema">
            <div className="tbl"><div className="name">runs</div><div className="cnt">{count('runs', live ? undefined : FACTS.runs)}</div><div className="dim">relational: space, fw, started_at, simulated</div>{!live && <div className="dim">{FACTS.runs_real} real, {FACTS.runs_simulated} simulated · {notLive}</div>}</div>
            <div className="tbl"><div className="name">events</div><div className="cnt">{count('events', live ? d.events_total : undefined)}</div><div className="dim">hypertable: verdicts (width_pass, width_fail), marks, obstacles</div></div>
            <div className="tbl"><div className="name">telem</div><div className="cnt">{count('telem')}</div><div className="dim">hypertable, columnstore: 10 Hz state, clearance, pose flag</div></div>
            <div className="tbl"><div className="name">scan</div><div className="cnt">{count('scan', live ? rows : FACTS.rows_total)}</div><div className="dim">hypertable, columnstore: one row per beam (time, deg, range_mm)</div>{!live && <div className="dim">{notLive}</div>}</div>
          </div>
          <p>One Postgres, no second system, no export step: the relational table, the time series, the aggregates and the compression live in the same database and join with a plain <span className="blue">using (run_id)</span>.</p>
          <div className="note">counts from the rows preset{queryUp ? ', refreshed every 20 s' : ' when the tailer answers'}<span className="sep">·</span>views runs_real, events_real, telem_real hide simulated runs</div>
        </Sec>

        <Sec n={4} title="Room over time" bullet="instant dashboards from continuous aggregates">
          <div className="aggs">
            <span className="k" style={{ alignSelf: 'center' }}>continuous aggregates</span>
            {d.aggregates.map((a) => <span key={a.name} className="agg">{a.name} <span className={a.real_time ? 'green' : 'dim'}>{a.real_time ? 'real-time' : 'materialized only'}</span>{live && a.rows > 0 && <span className="dim"> · {fmtInt(a.rows)} rows</span>}</span>)}
            {!live && <span className="dim" style={{ alignSelf: 'center' }}>{notLive}</span>}
          </div>
          <p className="dim" style={{ marginBottom: 8 }}>telem_15m, events_15m and scan_15m are 15-minute buckets per run kept current by TimescaleDB (policy every 10 minutes, refreshed after every upload); the tailer adds scan_1s. The history file and this chart read the aggregates, never the raw {fmtInt(live ? rows : FACTS.rows_total)} rows.</p>
          <div className="cols">
            <div><History /></div>
            <div>{bars ? <DoorChart bars={bars} source={barSource} /> : <div className="dim">no door history yet: the door_history preset did not answer and data/history.json is absent</div>}</div>
          </div>
        </Sec>

        <Sec n={5} title="Compression" bullet="90%+ compression on the free tier">
          <div className="bigs">
            <div><div className="k">raw estimate</div><div className="big">{fmtBytes(d.raw_bytes_est)}</div></div>
            <div><div className="k">on disk</div><div className="big green">{fmtBytes(d.on_disk_bytes)}</div></div>
            <div><div className="k">ratio</div><div className="big">{d.ratio.toFixed(1)}x</div></div>
            <div><div className="k">saved</div><div className="big green">{savedPct.toFixed(1)} %</div></div>
            {!live && <div><div className="k">source</div><div className="dim" style={{ lineHeight: '26px' }}>{notLive}</div></div>}
          </div>
          <div className="meter">
            <span className="k">raw</span><span className="mbar"><i style={{ width: '100%', background: C.dim }} /></span><span className="dim">{fmtBytes(d.raw_bytes_est)}</span>
            <span className="k">on disk</span><span className="mbar"><i style={{ width: `${Math.max(0.5, 100 / Math.max(1, d.ratio))}%`, background: C.green }} /></span><span className="green">{fmtBytes(d.on_disk_bytes)}</span>
          </div>
          <div className="note">
            {chunks.length
              ? chunks.map((c, i) => <span key={i}>{i > 0 && <span className="sep">·</span>}{c.table}: {c.compressed ?? '?'} of {c.total ?? '?'} chunks in columnstore{c.before && c.after ? ` (${(c.before / c.after).toFixed(1)}x by chunk stats)` : ''}</span>)
              : <>chunk counts come from the compression preset (hypertable_columnstore_stats) when the tailer answers</>}
          </div>
          <div className="note">raw estimate = rows × average tuple size (pg_column_size); on disk = hypertable_size(), what the storage bill sees. Rows are packed with COPY ... direct-compress (tech preview) in the tailer, then repacked chunk by chunk after each upload.</div>
        </Sec>

        <Sec n={6} title="Accuracy">
          <p>Doorway: Scout <span className="green">878 mm</span>, tape 880 mm (docs/ACCURACY.md). RPLIDAR A2M8 square to the frame; skewed 30 degrees it read 850 mm. Position is not part of this number: the clearance verdict needs no pose.</p>
        </Sec>

        <Sec n={7} title="Honesty">
          <p>Simulated runs (fw fake*) are tagged <span className="amber">simulated</span> in every table, refused by the uploader unless asked for, and hidden by default: the views and the history file exclude them.</p>
          <p>Position is never stored as truth: every telem row carries the <span className="blue">pose</span> flag, and a row with pose = false has x_mm, y_mm and heading_deg that mean nothing. SLAM did not hold a position on the real walks (docs/ACCURACY.md), so nothing here plots a map.</p>
          {live && queryText(d.query) && <p className="dim">timed query in tiger.json: {queryText(d.query)}</p>}
        </Sec>
      </div>
    </div>
  );
}
