import { useEffect, useRef, useState } from 'react';
import { useStore, type RunState } from './store';
import { clearBoard, send, sessionEvents, sessionRecording, sessionTelemetry, startRecording, stopRecording } from './scout';
import { widthRule, type ScoutEvent, type Telem } from './protocol';
import { detail as eventDetail } from './verdict';
import { MapView, mapImage } from './MapView';
import { blobToDataUrl, download, eventsCsv, fileStem, mapCsv, telemetryCsv } from './export';
import { buildReport } from './report';

const SPEED = 0.5, TURN = 0.5;
type Dir = 'f' | 'b' | 'l' | 'r';
const KEYS: Record<string, Dir> = { w: 'f', arrowup: 'f', s: 'b', arrowdown: 'b', a: 'l', arrowleft: 'l', d: 'r', arrowright: 'r' };

// What the feed calls each event kind. Only width kinds are verdicts.
const KIND: Record<string, string> = {
  width_fail: 'Too narrow', width_pass: 'Clear', ramp: 'Ramp', obstacle: 'Obstacle', mark: 'Mark', run_start: 'Run start', run_stop: 'Run stop',
};

export function Live() {
  const { telem, events, link, source, rules, recording, run, map, set } = useStore();
  const [space, setSpace] = useState('Room 1');
  const [pressedAt, setPressedAt] = useState(0);   // when Start run was pressed, to notice Scout not answering
  const drive = useDrive();

  const rule = widthRule(rules);
  const limit = rule?.limit ?? 860;
  const clearance = telem && telem.lidar && telem.clearance_mm > 0 ? telem.clearance_mm : null;
  const stale = source.kind === 'live' && link !== 'up';

  // The run is on if Scout says so (a page reload mid-run finds it still going) or if this
  // browser is recording one and Scout has not answered yet. A replay carries run_start and
  // run_stop of its own, but nothing here can start or stop it, so the button stays out of it.
  const live = source.kind === 'live';
  const active = live && (recording || run.active);
  const name = run.space || space.trim() || 'run';
  const tag = live ? runTag(run, recording, pressedAt, telem) : null;
  const elapsed = active && run.startT != null && telem ? clock(telem.t - run.startT) : '';

  // One run button. Start clears Scout's map and buffer and begins recording frames here. Stop
  // also stops the motors, so a run never ends with Scout still driving, and closes the recording.
  // Nothing downloads on its own: the Download menu, live once the run has stopped, hands out the
  // replay file and everything else on request.
  const toggleRun = () => {
    if (!active) { setPressedAt(Date.now()); startRecording(); send({ cmd: 'run', action: 'start', space: name }); return; }
    send({ cmd: 'run', action: 'stop' });
    send({ cmd: 'stop' });
    set({ run: { ...run, active: false } });
    stopRecording(name);
  };

  return (
    <>
      <div className="controls">
        <label className="field">Space <input value={space} onChange={(e) => setSpace(e.target.value)} placeholder="Room 1…" size={12} name="space" disabled={active} /></label>
        <button className={active ? 'rec' : 'primary'} aria-pressed={active} onClick={toggleRun} disabled={!live}>
          {active ? `Stop run${elapsed && ` ${elapsed}`}` : 'Start run'}
        </button>
        {tag && <span className={`tag ${tag.cls}`} role="status">{tag.text}</span>}
        <button onClick={() => send({ cmd: 'mark', label: 'mark' })}>Mark</button>
        <Downloads name={name} hasMap={map != null} active={active} />
        <button className="quiet end" onClick={() => { send({ cmd: 'map', action: 'clear' }); clearBoard(); }}>Clear map</button>
        {tag?.cls === 'bad' && (
          <p className="hint note" role="alert">
            {tag.text === 'No position'
              ? 'Scout has no position yet. Stop, move it where it can see more of the room, and start again.'
              : 'Scout did not confirm the run. A fake playing a file ignores commands; a live Scout that stays quiet has lost its link.'}
          </p>
        )}
      </div>

      {stale && <div className="stale-note" role="alert">Link down. Showing the last known values, not live.</div>}
      <div className={`stage ${stale ? 'stale' : ''}`}>
        <MapView />
        <aside>
          <Clearance value={clearance} limit={limit} source={rule?.source} status={telem && !telem.lidar ? 'No lidar' : undefined} />
          <Pad drive={drive} telem={telem} />
        </aside>
      </div>
      <section className={`sheet ${stale ? 'stale' : ''}`}>
        <div className="sheet-head"><h2>Events</h2></div>
        <ul className="feed">
          {events.length === 0 && <li className="empty">No events yet. Start a run and drive, or play a replay.</li>}
          {events.map((e) => <Row key={e.id} e={e} />)}
        </ul>
      </section>
    </>
  );
}

const clock = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

// What the run is doing, next to its button. Scout drops the room frame at run_start and reads it
// again from the first clean scan (pi/scout/pose.py), so the size that comes back is the one
// moment a bad lock is cheap to catch: check it against the tape measure.
function runTag(run: RunState, recording: boolean, pressedAt: number, telem: Telem | null): { text: string; cls: string } | null {
  if (!run.active) {
    if (!recording) return null;
    return Date.now() - pressedAt > 3000 ? { text: 'No answer from Scout', cls: 'bad' } : { text: 'Starting…', cls: 'warn' };
  }
  // startT is null for a run this browser did not see start, which a reload mid-run finds through
  // /status. There is no telling how long it has been waiting, so it makes no claim about the lock.
  if (run.startT == null) return { text: 'Run in progress', cls: 'plain' };
  if (!run.locked) {
    const waited = telem ? telem.t - run.startT : 0;
    return waited > 5000 ? { text: 'No position', cls: 'bad' } : { text: 'Finding position…', cls: 'warn' };
  }
  const room = telem?.room;
  return room ? { text: `Room ${(room.w_mm / 1000).toFixed(2)} x ${(room.l_mm / 1000).toFixed(2)} m`, cls: 'good' } : { text: 'Position locked', cls: 'good' };
}

// The run as one page a person can read, with the map drawn into it (report.ts).
async function saveReport(name: string) {
  const s = useStore.getState();
  const png = await mapImage();
  // the run ends at run_stop; before that it is still running, so it ends at the newest frame
  const stopped = [...sessionEvents()].reverse().find((e) => e.kind === 'run_stop');
  const end = (s.run.active ? s.telem?.t : stopped?.t ?? s.telem?.t) ?? 0;
  download(`${fileStem(name)}-report.html`, buildReport({
    space: s.run.space || name,
    startedAt: s.run.startedAt,
    durationMs: s.run.startT != null ? end - s.run.startT : 0,
    room: s.telem?.room ?? null,
    events: sessionEvents(),
    telemetry: sessionTelemetry(),
    trail: s.trail,
    rule: widthRule(s.rules),
    fw: s.fw,
    mapPng: png ? await blobToDataUrl(png) : null,
  }), 'text/html');
}

// The run as files: one page to read, and the tables behind it. Everything since run_start, or
// since connecting when no run was started. The replay file itself comes with Stop run.
// Greyed out while a run is going: a download is a snapshot, and the run is not finished until Stop.
function Downloads({ name, hasMap, active }: { name: string; hasMap: boolean; active: boolean }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const items = () => Array.from(wrap.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);
    items()[0]?.focus();
    const away = (e: PointerEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(false); trigger.current?.focus(); return; }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      e.preventDefault(); e.stopPropagation();   // arrows move through the list; they must not drive Scout
      const list = items(), i = list.indexOf(document.activeElement as HTMLButtonElement);
      list[(i + (e.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length]?.focus();
    };
    document.addEventListener('pointerdown', away);
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', away); document.removeEventListener('keydown', key); };
  }, [open]);

  const stem = () => fileStem(name);
  const items: [string, boolean, () => void][] = [
    ['Run report (.html)', hasMap || sessionEvents().length > 0, () => { void saveReport(name); }],
    ['Replay file (.ndjson)', sessionRecording() != null, () => { const r = sessionRecording(); if (r) download(`${fileStem(r.space)}.ndjson`, r.text, 'application/x-ndjson'); }],
    ['Events (.csv)', sessionEvents().length > 0, () => download(`${stem()}-events.csv`, eventsCsv(sessionEvents()), 'text/csv')],
    ['Telemetry (.csv)', sessionTelemetry().length > 0, () => download(`${stem()}-telemetry.csv`, telemetryCsv(sessionTelemetry()), 'text/csv')],
    ['Map cells (.csv)', hasMap, () => { const m = useStore.getState().map; if (m) download(`${stem()}-map-${m.cell_mm}mm.csv`, mapCsv(m), 'text/csv'); }],
    ['Map image (.png)', hasMap, () => { void mapImage().then((b) => { if (b) download(`${stem()}-map.png`, b); }); }],
  ];
  return (
    <div className="menu" ref={wrap}>
      <button ref={trigger} aria-haspopup="menu" aria-expanded={open} disabled={active} title={active ? 'Stop the run first' : undefined} onClick={() => setOpen((o) => !o)}>
        Download
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1.5 3.5 5 7l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
      </button>
      {open && (
        <div role="menu" className="menu-list" aria-label="Download">
          {items.map(([label, ok, fn]) => (
            <button key={label} role="menuitem" disabled={!ok} onClick={() => { setOpen(false); fn(); }}>{label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function Row({ e }: { e: ScoutEvent }) {
  const cls = e.kind === 'width_fail' ? 'fail' : e.kind === 'width_pass' ? 'pass'
    : e.kind === 'ramp' || e.kind === 'mark' ? 'mark' : 'info';
  return (
    <li className={cls}>
      <span className="kind">{KIND[e.kind] ?? e.kind}</span>
      <span>{eventDetail(e)}</span>
      <span className="t">{(e.t / 1000).toFixed(1)}s</span>
    </li>
  );
}

function Clearance({ value, limit, source, status }: { value: number | null; limit: number; source?: string; status?: string }) {
  const bad = value != null && value < limit;
  const pct = value == null ? 0 : Math.max(0, Math.min(1, value / (2 * limit)));
  // the verdict is text as well as colour; "No lidar" takes precedence when Scout says so
  const tag = status ? { text: status, cls: 'warn' } : value == null ? null : bad ? { text: 'Too narrow', cls: 'bad' } : { text: 'Clear', cls: 'good' };
  return (
    <section className={`sheet gauge ${value == null ? '' : bad ? 'bad' : 'good'}`} aria-live="polite">
      <div className="sheet-head"><h2>Clearance</h2>{tag && <span className={`tag ${tag.cls}`}>{tag.text}</span>}</div>
      <div className="gauge-body">
        {value == null
          ? <div className="big none">no gap</div>   /* an open room, or the way ahead blocked: nothing to measure across (PROTOCOL.md section 6) */
          : <div className="big">{value}<small>mm</small></div>}
        <div className="bar" role="img" aria-label={value == null ? 'no measurement' : `${value} of ${2 * limit} mm, limit at ${limit}`}>
          <div className="fill" style={{ width: `${pct * 100}%` }} /><div className="limit" style={{ left: '50%' }} />
        </div>
        <div className="sub">A wheelchair needs {limit}&nbsp;mm{source ? ` · ${source}` : ''}</div>
      </div>
    </section>
  );
}

// Teleop. While any key or button is held, resend drive every 100 ms (PROTOCOL.md section 5).
// Release sends stop. Space is E-STOP. Losing window focus sends stop.
function useDrive() {
  const held = useRef(new Set<Dir>());
  const timer = useRef(0);

  const tick = () => {
    const h = held.current;
    send({ cmd: 'drive', v: ((h.has('f') ? 1 : 0) - (h.has('b') ? 1 : 0)) * SPEED, w: ((h.has('l') ? 1 : 0) - (h.has('r') ? 1 : 0)) * TURN });
  };
  const stopAll = () => {
    held.current.clear();
    clearInterval(timer.current); timer.current = 0;
    send({ cmd: 'stop' });
  };
  const press = (d: Dir) => {
    held.current.add(d);
    if (!timer.current) { tick(); timer.current = window.setInterval(tick, 100); }
  };
  const release = (d: Dir) => {
    if (!held.current.delete(d)) return;
    if (held.current.size === 0) stopAll();
  };

  useEffect(() => {
    const typing = (e: KeyboardEvent) => ['INPUT', 'SELECT', 'TEXTAREA'].includes((e.target as HTMLElement)?.tagName);
    const down = (e: KeyboardEvent) => {
      if (typing(e)) return;
      if (e.key === ' ') { e.preventDefault(); stopAll(); return; }
      const d = KEYS[e.key.toLowerCase()];
      if (d) { e.preventDefault(); if (!e.repeat) press(d); }
    };
    const up = (e: KeyboardEvent) => { const d = KEYS[e.key.toLowerCase()]; if (d) release(d); };
    const blur = () => { if (held.current.size) stopAll(); };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', blur);
      clearInterval(timer.current); timer.current = 0;
    };
    // handlers only touch refs and stable functions, so registering once is right
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { press, release, stopAll };
}

const NAME: Record<Dir, string> = { f: 'Forward', b: 'Back', l: 'Left', r: 'Right' };
const ROT: Record<Dir, number> = { f: 0, r: 90, b: 180, l: 270 };

// What Scout is doing, as a stamp: driven by hand, idle, or wall following (still in the protocol, no button).
const MODE: Record<string, { text: string; cls: string }> = {
  wall_follow: { text: 'Roaming', cls: 'good' }, teleop: { text: 'Manual', cls: 'warn' }, idle: { text: 'Idle', cls: 'plain' },
};

function Pad({ drive, telem }: { drive: ReturnType<typeof useDrive>; telem: Telem | null }) {
  const mode = !telem ? null : telem.measuring ? { text: 'Confirming…', cls: 'warn' } : MODE[telem.mode] ?? { text: telem.mode, cls: 'plain' };
  const b = (d: Dir) => (
    <button aria-label={NAME[d]} onPointerDown={() => drive.press(d)} onPointerUp={() => drive.release(d)} onPointerLeave={() => drive.release(d)}
      onContextMenu={(e) => e.preventDefault()}>
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" style={{ transform: `rotate(${ROT[d]}deg)` }}>
        <path d="M10 3.5 16 10l-1.4 1.4L11 7.8V16.5H9V7.8L5.4 11.4 4 10z" fill="currentColor" />
      </svg>
    </button>
  );
  return (
    <section className="sheet padwrap">
      <div className="sheet-head"><h2>Drive</h2>{mode && <span className={`tag ${mode.cls}`} role="status">{mode.text}</span>}</div>
      <div className="pad-body">
        <div className="pad">
          <span />{b('f')}<span />
          {b('l')}<span />{b('r')}
          <span />{b('b')}<span />
        </div>
        <p className="hint">Arrow keys or WASD drive. Let go and Scout stops. Space is the emergency stop, from anywhere on the page.</p>
      </div>
    </section>
  );
}
