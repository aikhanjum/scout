import { useEffect, useRef, useState } from 'react';
import { useStore } from './store';
import { send, startRecording, stopRecording } from './scout';
import { widthRule, type ScoutEvent, type Telem } from './protocol';
import { detail as eventDetail } from './verdict';
import { MapView } from './MapView';

const SPEED = 0.5, TURN = 0.5;
type Dir = 'f' | 'b' | 'l' | 'r';
const KEYS: Record<string, Dir> = { w: 'f', arrowup: 'f', s: 'b', arrowdown: 'b', a: 'l', arrowleft: 'l', d: 'r', arrowright: 'r' };

// What the feed calls each event kind. Only width kinds are verdicts.
const KIND: Record<string, string> = {
  width_fail: 'Too narrow', width_pass: 'Clear', ramp: 'Ramp', obstacle: 'Obstacle', mark: 'Mark', run_start: 'Run start', run_stop: 'Run stop',
};

export function Live() {
  const { telem, events, link, source, rules, recording, set } = useStore();
  const [space, setSpace] = useState('Room 1');
  const drive = useDrive();

  const rule = widthRule(rules);
  const limit = rule?.limit ?? 860;
  const clearance = telem && telem.lidar && telem.clearance_mm > 0 ? telem.clearance_mm : null;
  const stale = source.kind === 'live' && link !== 'up';
  const roaming = telem?.mode === 'wall_follow';

  // One run button. Starting a run also records the frames in the browser; stopping saves them
  // as an .ndjson download, so every run leaves a replay file behind without a second button.
  const toggleRun = () => {
    const name = space.trim() || 'run';
    if (!recording) { startRecording(); send({ cmd: 'run', action: 'start', space: name }); return; }
    send({ cmd: 'run', action: 'stop' });
    const text = stopRecording(name);
    if (!text) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'application/x-ndjson' }));
    a.download = `${name.replace(/\W+/g, '-').toLowerCase()}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.ndjson`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <>
      <div className="controls">
        <button aria-pressed={roaming} onClick={() => send({ cmd: 'mode', mode: roaming ? 'idle' : 'wall_follow' })}>Roam</button>
        <span className="gap" />
        <label className="field">Space <input value={space} onChange={(e) => setSpace(e.target.value)} placeholder="Room 1…" size={12} name="space" /></label>
        <button className={recording ? 'rec' : 'primary'} aria-pressed={recording} onClick={toggleRun}>{recording ? 'Stop run' : 'Start run'}</button>
        <button onClick={() => send({ cmd: 'mark', label: 'mark' })}>Mark</button>
        <button className="quiet end" onClick={() => { send({ cmd: 'map', action: 'clear' }); set({ trail: [] }); }}>Clear map</button>
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

function Row({ e }: { e: ScoutEvent }) {
  const baseUrl = useStore((s) => s.baseUrl);
  const cls = e.kind === 'width_fail' ? 'fail' : e.kind === 'width_pass' ? 'pass'
    : e.kind === 'ramp' || e.kind === 'mark' ? 'mark' : 'info';
  return (
    <li className={cls}>
      <span className="kind">{KIND[e.kind] ?? e.kind}</span>
      <span>
        {eventDetail(e)}
        {e.photo && baseUrl && (
          // the still Scout kept when it classified this. Missing on a fake or a replay: just hide it.
          <img className="shot" src={`${baseUrl}/photo/${e.photo}`} alt="" onError={(ev) => { ev.currentTarget.style.display = 'none'; }} />
        )}
      </span>
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
  const [vec, setVec] = useState({ v: 0, w: 0 });

  const tick = () => {
    const h = held.current;
    const cmd = { v: ((h.has('f') ? 1 : 0) - (h.has('b') ? 1 : 0)) * SPEED, w: ((h.has('l') ? 1 : 0) - (h.has('r') ? 1 : 0)) * TURN };
    setVec(cmd);
    send({ cmd: 'drive', ...cmd });
  };
  const stopAll = () => {
    held.current.clear();
    clearInterval(timer.current); timer.current = 0;
    setVec({ v: 0, w: 0 });
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

  return { vec, press, release, stopAll };
}

const NAME: Record<Dir, string> = { f: 'Forward', b: 'Back', l: 'Left', r: 'Right' };
const ROT: Record<Dir, number> = { f: 0, r: 90, b: 180, l: 270 };

// What Scout is doing, as a stamp: holding still to look, roaming on its own, driven by hand, or idle.
const MODE: Record<string, { text: string; cls: string }> = {
  wall_follow: { text: 'Roaming', cls: 'good' }, teleop: { text: 'Manual', cls: 'warn' }, idle: { text: 'Idle', cls: 'plain' },
};

function Pad({ drive, telem }: { drive: ReturnType<typeof useDrive>; telem: Telem | null }) {
  const mode = !telem ? null : telem.measuring ? { text: 'Looking…', cls: 'warn' } : MODE[telem.mode] ?? { text: telem.mode, cls: 'plain' };
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
          {b('l')}<button className="danger" aria-label="Emergency stop" onClick={drive.stopAll}>Stop</button>{b('r')}
          <span />{b('b')}<span />
        </div>
        <p className="hint">Arrow keys or WASD drive. Space stops everything. Driving takes over from roaming.</p>
      </div>
    </section>
  );
}
