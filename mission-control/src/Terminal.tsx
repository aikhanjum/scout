// OWNER: terminal agent. The Bloomberg-style screen: one page, everything visible, no scroll.
// Composes the eye, radar, tiger, QR and history components; owns the status, health, gaps,
// clearance, events, session and clock tiles, and terminal.css.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useStore } from './store';
import { send, startRecording, stopRecording } from './scout';
import { isPlaced, passes, widthRule, type ScoutEvent } from './protocol';
import { distanceColour } from './palette';
import { download, fileStem } from './export';
import { Tabs } from './Tabs';
import { SourceBar } from './SourceBar';
import { ScoutEye } from './ScoutEye';
import { Radar } from './Radar';
import { TigerPanel } from './TigerPanel';
import { QrCard } from './QrCard';
import { History } from './History';
import { Drive } from './Drive';
import './terminal.css';

// Receipt bookkeeping the store does not keep: when each telem frame and each event reached this
// browser. Frame age and frames/s describe the link, so they are measured here, never read off t.
const rx = { last: 0, times: [] as number[], frames: 0, events: 0, eventAt: new Map<number, number>() };
useStore.subscribe((s, p) => {
  if (s.source !== p.source) { rx.last = 0; rx.times = []; rx.frames = 0; rx.events = 0; rx.eventAt.clear(); }
  if (s.telem && s.telem !== p.telem) {
    const now = Date.now();
    rx.last = now; rx.frames += 1; rx.times.push(now);
    if (rx.times.length > 80) rx.times.splice(0, rx.times.length - 80);
  }
  if (s.events !== p.events) {
    const now = Date.now();
    for (const e of s.events) if (!rx.eventAt.has(e.id)) { rx.eventAt.set(e.id, now); rx.events += 1; }
    if (rx.eventAt.size > 400) for (const k of [...rx.eventAt.keys()].slice(0, 200)) rx.eventAt.delete(k);
  }
});

const T0 = Date.now();
const pad2 = (n: number) => String(n).padStart(2, '0');
const hms = (d: Date) => `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
const dur = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${pad2(Math.floor(s / 3600))}:${pad2(Math.floor(s / 60) % 60)}:${pad2(s % 60)}`; };
const mmss = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${pad2(Math.floor(s / 60))}:${pad2(s % 60)}`; };
const signed = (x: number) => (x > 0 ? '+' : '') + x.toFixed(2);
// scan index -> bearing name. Index i is i degrees counter-clockwise of straight ahead (section 6).
const bearingAt = (i: number) => (i === 0 ? 'F' : i === 180 ? 'B' : i < 180 ? `L${i}` : `R${360 - i}`);
// a gap edge in degrees, either convention, -> the same names
const bearingDeg = (a: number) => { const r = Math.round((((a + 180) % 360) + 360) % 360 - 180); return r === 0 ? 'F' : Math.abs(r) === 180 ? 'B' : r > 0 ? `L${r}` : `R${-r}`; };

function useTick(ms: number) {
  const [, set] = useState(0);
  useEffect(() => { const id = setInterval(() => set((n) => n + 1), ms); return () => clearInterval(id); }, [ms]);
}

// One tile: a fixed cell of the grid, a 16 px label line, a body that clips. fill: the child is
// another agent's component and is stretched to the body; body: an extra class on the body.
function Tile({ label, right, col, row, cls, fill, body, children }: { label: string; right?: ReactNode; col: string; row: number; cls?: string; fill?: boolean; body?: string; children: ReactNode }) {
  return (
    <section className={`tile${cls ? ` ${cls}` : ''}`} style={{ gridColumn: col, gridRow: row }} aria-label={label}>
      <div className="tl">{label}{right != null && <span className="r">{right}</span>}</div>
      <div className={`tb${fill ? ' fill' : ''}${body ? ` ${body}` : ''}`}>{children}</div>
    </section>
  );
}

export function Terminal() {
  const source = useStore((s) => s.source);
  const link = useStore((s) => s.link);
  const fw = useStore((s) => s.fw);
  const gapCount = useStore((s) => s.telem?.gaps.length ?? 0);
  const seeThrough = useStore((s) => s.telem?.gaps.filter((g) => g.evidence === 'see_through').length ?? 0);

  const live = source.kind === 'live';
  const stale = live && link !== 'up';
  const badge = !live ? ['REPLAY', 'replay'] : link !== 'up' ? ['LINK DOWN', 'down'] : fw.startsWith('fake') ? ['SIMULATED', 'sim'] : ['LIVE', 'live'];
  const st = stale ? 'stale' : undefined;

  return (
    <div className="term">
      <header className="th">
        <span className="brand">SCOUT</span>
        <span className="sep">|</span>
        <Tabs />
        <span className="sep">|</span>
        <SourceBar />
        <span className={`badge ${badge[1]}`} role="status">{badge[0]}</span>
        <Clock />
      </header>
      <div className="tg">
        <Tile label="Eyes" right="front 120°, telem.scan" col="1 / 8" row={1} cls={st} fill><ScoutEye /></Tile>
        <Tile label="Radar" right="360°, robot frame" col="8 / 11" row={1} cls={st} fill><Radar /></Tile>
        <Tile label="Clearance" right="clearance_mm" col="11 / 13" row={1} cls={st}><ClearanceTile /></Tile>

        <Tile label="Status" col="1 / 4" row={2} cls={st}><StatusTile /></Tile>
        <Tile label="Lidar health" right="360 bins, second-smallest return" col="4 / 7" row={2} cls={st}><HealthTile /></Tile>
        <Tile label="Gaps" right={`${gapCount} gap${gapCount === 1 ? '' : 's'} · ${seeThrough} see-through`} col="7 / 10" row={2} cls={st}><GapsTile /></Tile>
        <Tile label="Tiger Data" col="10 / 13" row={2} fill><TigerPanel /></Tile>

        <Tile label="Events" right="newest first" col="1 / 4" row={3}><EventsTile /></Tile>
        <Tile label="Session" col="4 / 6" row={3}><SessionTile /></Tile>
        <Tile label="Room over time" right="data/history.json" col="6 / 9" row={3} body="hist-wrap"><History /></Tile>
        <Tile label="Drive" col="9 / 11" row={3}><Drive /></Tile>
        <Tile label="Phone" col="11 / 13" row={3} fill><QrCard /></Tile>
      </div>
    </div>
  );
}

function Clock() {
  useTick(1000);
  return <span className="clock"><b>{hms(new Date())}</b>UP {dur(Date.now() - T0)}</span>;
}

// The live clearance readout. 0 is not a number: it is Scout refusing to measure (section 6).
function ClearanceTile() {
  const telem = useStore((s) => s.telem);
  const rules = useStore((s) => s.rules);
  const rule = widthRule(rules);
  const limit = rule?.limit ?? 860;
  const noLidar = telem != null && !telem.lidar;
  const value = telem && telem.lidar && telem.clearance_mm > 0 ? telem.clearance_mm : null;
  const ok = value != null && (rule ? passes(rule, value) : value >= limit);
  return (
    <div className="clr">
      {value != null ? (
        <>
          <div className="big">{value}<small>mm</small></div>
          <span className={`verdict ${ok ? 'pass' : 'fail'}`}>{ok ? 'PASS' : 'FAIL'} · {ok ? '≥' : '<'} {limit} mm</span>
        </>
      ) : (
        <>
          <div className="big c-dim">{'—'}</div>
          <div className="lab" style={{ marginTop: 6 }}>{telem == null ? 'no telemetry' : noLidar ? 'no lidar' : 'no measurement'}</div>
          {telem != null && !noLidar && <div className="why">refused: obstacle in the front cone or a side with no returns</div>}
        </>
      )}
      <div className="rulecard">
        <div><b>LIMIT {limit} mm</b></div>
        <div>{rule?.text ?? 'rules.json not loaded'}</div>
        {rule?.source && <div className="src">{rule.source}</div>}
        <div><span className="lab">measured</span> perpendicular bounds within ±45°</div>
      </div>
    </div>
  );
}

function StatusTile() {
  useTick(100);
  const telem = useStore((s) => s.telem);
  const link = useStore((s) => s.link);
  const fw = useStore((s) => s.fw);
  const detail = useStore((s) => s.detail);
  const now = Date.now();
  const age = rx.last ? now - rx.last : null;
  const fps = rx.times.filter((t) => now - t <= 2000).length / 2;
  const mode = !telem ? '—' : telem.mode === 'wall_follow' ? 'ROAM' : telem.mode.toUpperCase();
  const modeCls = !telem ? 'c-dim' : telem.mode === 'wall_follow' ? 'c-green' : telem.mode === 'teleop' ? 'c-amber' : 'c-dim';
  const ageCls = age == null ? 'c-dim' : age > 2000 ? 'c-red' : age > 400 ? 'c-amber' : '';
  const dash = '—';
  return (
    <div className="kv">
      <span className="k">Link</span><span className={`v ${link === 'up' ? 'c-green' : 'c-red'}`}>{link.toUpperCase()}{detail && <span className="c-dim"> {detail}</span>}</span>
      <span className="k">FW</span><span className="v" title={fw}>{fw || dash}</span>
      <span className="k">Mode</span><span className={`v ${modeCls}`}>{mode}{telem?.measuring && <span className="c-amber"> MEASURING</span>}</span>
      <span className="k">Lidar</span><span className={`v ${telem && !telem.lidar ? 'c-red' : ''}`}>{telem ? (telem.lidar ? 'ON' : 'OFF') : dash}</span>
      <span className="k">Frame age</span><span className={`v ${ageCls}`}>{age == null ? dash : `${age} ms`}</span>
      <span className="k">Frames/s</span><span className="v">{fps.toFixed(1)}</span>
      <span className="k">V</span><span className="v">{telem ? signed(telem.v) : dash}</span>
      <span className="k">W</span><span className="v">{telem ? signed(telem.w) : dash}</span>
      <span className="k">Bump</span><span className="v">{telem ? `${telem.bump[0]} ${telem.bump[1]}` : dash}</span>
      <span className="k">Stuck</span><span className={`v ${telem?.stuck ? 'c-red' : ''}`}>{telem ? (telem.stuck ? 'YES' : 'no') : dash}</span>
      <span className="k">Pose</span>
      <span className="v wide">{telem?.pose ? `x ${telem.x_mm}  y ${telem.y_mm}  h ${telem.heading_deg.toFixed(0)}°` : <span className="c-dim">none</span>}</span>
    </div>
  );
}

type Quad = 'F' | 'L' | 'B' | 'R';
function scanStats(scan: number[] | undefined) {
  if (!scan || scan.length === 0) return null;
  const n = scan.length;
  const q: Record<Quad, number> = { F: 0, L: 0, B: 0, R: 0 };
  const vals: number[] = [];
  let near = 0, nearI = -1, far = 0, farI = -1;
  for (let i = 0; i < n; i++) {
    const r = scan[i];
    if (r <= 0) continue;
    vals.push(r);
    if (nearI < 0 || r < near) { near = r; nearI = i; }
    if (r > far) { far = r; farI = i; }
    const k: Quad = i <= 45 || i >= 315 ? 'F' : i <= 135 ? 'L' : i <= 225 ? 'B' : 'R';   // F = ±45°, L = 45–135, B = 135–225, R = 225–315
    if (!q[k] || r < q[k]) q[k] = r;
  }
  vals.sort((a, b) => a - b);
  return { n, returns: vals.length, near, nearI, far, farI, median: vals.length ? vals[vals.length >> 1] : 0, q };
}

function HealthTile() {
  const scan = useStore((s) => s.telem?.scan);
  const canvas = useRef<HTMLCanvasElement>(null);
  const st = scanStats(scan);

  // one column per degree, height ∝ 1/range, left of the robot on the left, straight ahead in the middle
  useEffect(() => {
    const c = canvas.current; const ctx = c?.getContext('2d');
    if (!c || !ctx) return;
    ctx.clearRect(0, 0, c.width, c.height);
    if (!scan || scan.length !== 360) return;
    for (let x = 0; x < 360; x++) {
      const r = scan[(((180 - x) % 360) + 360) % 360];
      if (r <= 0) continue;
      const h = Math.max(1, Math.min(24, Math.round(24 * 400 / r)));
      ctx.fillStyle = distanceColour(r);
      ctx.fillRect(x, 24 - h, 1, h);
    }
  }, [scan]);

  const dash = '—';
  const pct = st ? Math.round((st.returns / st.n) * 100) : 0;
  const mm = (v: number) => (v > 0 ? `${v} mm` : dash);
  return (
    <>
      <div className="kv">
        <span className="k">Returns</span><span className="v">{st ? `${st.returns}/${st.n}  ${pct}%` : dash}</span>
        <span className="k">Empty</span><span className={`v ${st && 100 - pct > 50 ? 'c-amber' : ''}`}>{st ? `${100 - pct}%` : dash}</span>
        <span className="k">Nearest</span><span className="v" style={st && st.near ? { color: distanceColour(st.near) } : undefined}>{st && st.nearI >= 0 ? `${st.near} mm @ ${bearingAt(st.nearI)}` : dash}</span>
        <span className="k">Farthest</span><span className="v">{st && st.farI >= 0 ? `${st.far} mm @ ${bearingAt(st.farI)}` : dash}</span>
        <span className="k">Median</span><span className="v">{st ? mm(st.median) : dash}</span>
        <span className="v quad">
          <span className="k">Nearest per quadrant</span>
          {(['F', 'L', 'B', 'R'] as Quad[]).map((k) => (
            <span key={k} style={st && st.q[k] ? { color: distanceColour(st.q[k]) } : undefined}><b>{k}</b>{st && st.q[k] ? `${st.q[k]} mm` : dash}</span>
          ))}
        </span>
      </div>
      <canvas ref={canvas} className="strip" width={360} height={24} aria-label="scan strip, one column per degree" />
      <div className="stripx"><span>REAR</span><span>LEFT</span><span>FRONT</span><span>RIGHT</span><span>REAR</span></div>
    </>
  );
}

const EVIDENCE: Record<string, [string, string]> = { see_through: ['SEE-THRU', 'c-green'], step: ['STEP', 'c-amber'], unverified: ['UNVERIFIED', 'c-dim'] };

// This frame's gaps. Only see_through is a measured opening (PROTOCOL.md section 6); the rest
// get a dash where the width would be, never a number.
function GapsTile() {
  const gaps = useStore((s) => s.telem?.gaps);
  const lidar = useStore((s) => s.telem?.lidar);
  if (!gaps || gaps.length === 0) return <div className="empty">{lidar === false ? 'no lidar' : 'no openings in this scan'}</div>;
  const rows = gaps.slice().sort((a, b) => b.width_mm - a.width_mm);
  return (
    <div className="rows">
      {rows.map((g, i) => {
        const [tag, cls] = EVIDENCE[g.evidence] ?? [g.evidence.toUpperCase(), 'c-dim'];
        const measured = g.evidence === 'see_through';
        return (
          <div className="row gap" key={i}>
            <span>{bearingDeg(g.a0)}–{bearingDeg(g.a1)}</span>
            <span className="c-dim">{g.span_deg.toFixed(1)}°</span>
            <span style={measured ? { color: distanceColour(g.width_mm) } : undefined} className={measured ? '' : 'c-dim'}>{measured ? `${g.width_mm} mm` : '—'}</span>
            <span className={`tag2 ${cls}`}>{tag}</span>
          </div>
        );
      })}
    </div>
  );
}

const LABEL: Record<string, [string, string]> = {
  width_fail: ['TOO NARROW', 'c-red'], width_pass: ['CLEAR', 'c-green'], obstacle: ['OBSTACLE', 'c-amber'], ramp: ['RAMP', 'c-amber'],
  mark: ['MARK', 'c-blue'], run_start: ['RUN START', 'c-dim'], run_stop: ['RUN STOP', 'c-dim'], bump: ['BUMP', 'c-red'], stuck: ['STUCK', 'c-red'],
};

function eventValue(e: ScoutEvent, limit: number) {
  if (e.kind === 'width_pass' || e.kind === 'width_fail') {
    const lim = e.limit ?? limit, v = e.value ?? 0;
    return `${v} ${v < lim ? '<' : '≥'} ${lim} mm${e.between ? ` · ${e.between}` : ''}`;
  }
  if (e.kind === 'obstacle' || e.kind === 'ramp') {
    const at = isPlaced(e) && typeof e.x_mm === 'number' && typeof e.y_mm === 'number' ? `at ${(e.x_mm / 1000).toFixed(2)}, ${(e.y_mm / 1000).toFixed(2)} m` : 'no position';
    return e.label && e.label !== 'unknown' ? `${e.label} ${at}` : at;
  }
  if (e.kind === 'mark') return e.label ?? '';
  return e.space || '';
}

function EventsTile() {
  const events = useStore((s) => s.events);
  const rules = useStore((s) => s.rules);
  const limit = widthRule(rules)?.limit ?? 860;
  if (events.length === 0) return <div className="empty">no events yet · start a run and drive, or play a replay</div>;
  return (
    <div className="rows">
      {events.map((e) => {
        const [label, cls] = LABEL[e.kind] ?? [e.kind.toUpperCase(), 'c-dim'];
        const at = rx.eventAt.get(e.id);
        return (
          <div className="row ev" key={e.id}>
            <span className="c-dim">{at ? hms(new Date(at)) : '—'}</span>
            <span className={`tag2 ${cls}`}>{label}</span>
            <span className="val" title={eventValue(e, limit)}>{eventValue(e, limit)}</span>
          </div>
        );
      })}
    </div>
  );
}

// The run controls, as Live.tsx does them: start = record here + run start on Scout; stop = run
// stop, motors stop, close the recording. The terminal has no Download menu, so the .ndjson is
// handed out right there.
function SessionTile() {
  useTick(500);
  const telem = useStore((s) => s.telem);
  const source = useStore((s) => s.source);
  const recording = useStore((s) => s.recording);
  const run = useStore((s) => s.run);
  const voice = useStore((s) => s.voice);
  const set = useStore((s) => s.set);
  const [space, setSpace] = useState('Room 1');
  const [pressedAt, setPressedAt] = useState(0);
  const recFrom = useRef(0);
  const [lastRec, setLastRec] = useState(0);

  const live = source.kind === 'live';
  const active = live && (recording || run.active);
  const name = run.space || space.trim() || 'run';

  const toggleRun = () => {
    if (!active) { setPressedAt(Date.now()); recFrom.current = rx.frames; startRecording(); send({ cmd: 'run', action: 'start', space: name }); return; }
    send({ cmd: 'run', action: 'stop' });
    send({ cmd: 'stop' });
    set({ run: { ...run, active: false } });
    setLastRec(rx.frames - recFrom.current);
    const text = stopRecording(name);
    if (text) download(`${fileStem(name)}.ndjson`, text, 'application/x-ndjson');
  };

  const tag: [string, string] = run.active
    ? [`ON ${run.startT != null && telem ? mmss(telem.t - run.startT) : ''}`.trim(), 'c-green']
    : recording ? (Date.now() - pressedAt > 3000 ? ['NO ANSWER FROM SCOUT', 'c-red'] : ['STARTING…', 'c-amber']) : ['OFF', 'c-dim'];
  const recFrames = recording ? rx.frames - recFrom.current : lastRec;
  const kb = recFrames * 1.5;
  const size = recFrames === 0 ? '—' : kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.round(kb)} KB`;

  return (
    <div className="sess">
      <div className="line">
        <span className="lab">Space</span>
        <input value={space} onChange={(e) => setSpace(e.target.value)} placeholder="Room 1" name="space" disabled={active} />
      </div>
      <div className="line">
        <button type="button" className={`btn2 ${active ? 'rec' : 'go'}`} onClick={toggleRun} disabled={!live}>{active ? 'STOP RUN' : 'START RUN'}</button>
        <button type="button" className="btn2 mark" onClick={() => send({ cmd: 'mark', label: 'mark' })} disabled={!live}>MARK</button>
      </div>
      <div className="kv">
        <span className="k">Run</span><span className={`v ${tag[1]}`}>{tag[0]}{run.space && run.active ? <span className="c-dim"> · {run.space}</span> : null}</span>
        <span className="k">Frames</span><span className="v">{rx.frames}<span className="c-dim"> this session</span></span>
        <span className="k">Events</span><span className="v">{rx.events}</span>
        <span className="k">Rec</span><span className={`v ${recording ? 'c-red' : ''}`}>{size}{recording ? ' · recording' : recFrames ? ' · saved' : ''}<span className="c-dim"> {recFrames ? `(${recFrames} × 1.5 KB)` : ''}</span></span>
        <span className="k">Voice</span><span className="v"><button type="button" className={`btn2 sm${voice ? ' lit' : ''}`} aria-pressed={voice} onClick={() => set({ voice: !voice })}>{voice ? 'ON' : 'OFF'}</button></span>
      </div>
    </div>
  );
}
