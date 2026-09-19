import { useEffect, useRef, useState } from 'react';
import { useStore } from './store';
import { send, startRecording, stopRecording } from './scout';
import { widthRule, type ScoutEvent, type Telem } from './protocol';
import { detail as eventDetail } from './verdict';
import { MapView } from './MapView';
import { LidarView } from './LidarView';
import { History } from './History';

const SPEED = 0.5, TURN = 0.5;
type Dir = 'f' | 'b' | 'l' | 'r';
const KEYS: Record<string, Dir> = { w: 'f', arrowup: 'f', s: 'b', arrowdown: 'b', a: 'l', arrowleft: 'l', d: 'r', arrowright: 'r' };

export function Live() {
  const { telem, events, link, source, rules, recording } = useStore();
  const [space, setSpace] = useState('Room 1');
  const [mark, setMark] = useState('');
  const drive = useDrive();

  const limit = widthRule(rules)?.limit ?? 860;
  const clearance = telem && telem.lidar && telem.clearance_mm > 0 ? telem.clearance_mm : null;
  const stale = source.kind === 'live' && link !== 'up';
  const roaming = telem?.mode === 'wall_follow';

  const toggleRec = () => {
    if (!recording) return startRecording();
    const text = stopRecording(space);
    if (!text) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'application/x-ndjson' }));
    a.download = `${space.replace(/\W+/g, '-').toLowerCase() || 'run'}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.ndjson`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <>
      <div className="controls">
        <button className={roaming ? 'on' : ''} onClick={() => send({ cmd: 'mode', mode: 'wall_follow' })}>ROAM</button>
        <button onClick={() => send({ cmd: 'mode', mode: 'idle' })}>IDLE</button>
        <span className="spacer" />
        <input value={space} onChange={(e) => setSpace(e.target.value)} placeholder="space name" size={12} />
        <button onClick={() => send({ cmd: 'run', action: 'start', space })}>Start run</button>
        <button onClick={() => send({ cmd: 'run', action: 'stop' })}>Stop run</button>
        <input value={mark} onChange={(e) => setMark(e.target.value)} placeholder="mark label" size={10} />
        <button onClick={() => send({ cmd: 'mark', label: mark || 'mark' })}>MARK</button>
        <button onClick={() => send({ cmd: 'map', action: 'clear' })}>Clear map</button>
        <button onClick={() => send({ cmd: 'beep' })}>BEEP</button>
        <button className={recording ? 'on' : ''} onClick={toggleRec}>{recording ? '■ Save recording' : '● REC'}</button>
      </div>

      {stale && <div className="stale-note">LINK DOWN. Last known values, not live.</div>}
      <div className={`stage ${stale ? 'stale' : ''}`}>
        <div className="views">
          <MapView />
          <LidarView />
          <History />
        </div>
        <aside>
          <Clearance value={clearance} limit={limit} tag={telem?.measuring ? 'LOOKING' : telem && !telem.lidar ? 'NO LIDAR' : undefined} />
          <Pad drive={drive} telem={telem} />
          <ul className="feed">
            {events.length === 0 && <li className="info"><span className="kind">·</span><span className="muted">No events yet</span><span /></li>}
            {events.map((e) => <Row key={e.id} e={e} />)}
          </ul>
        </aside>
      </div>
    </>
  );
}

function Row({ e }: { e: ScoutEvent }) {
  const baseUrl = useStore((s) => s.baseUrl);
  const cls = e.kind === 'width_fail' ? 'fail' : e.kind === 'width_pass' ? 'pass'
    : e.kind === 'ramp' || e.kind === 'mark' ? 'mark' : 'info';
  return (
    <li className={cls}>
      <span className="kind">{e.kind}</span>
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

function Clearance({ value, limit, tag }: { value: number | null; limit: number; tag?: string }) {
  const bad = value != null && value < limit;
  const pct = value == null ? 0 : Math.max(0, Math.min(1, value / (2 * limit)));
  return (
    <div className={`gauge ${value == null ? '' : bad ? 'bad' : 'good'}`}>
      <div className="gauge-head"><span>CLEARANCE</span>{tag && <span className="tag">{tag}</span>}</div>
      <div className="big">{value ?? '--'}<small>mm</small></div>
      <div className="bar"><div className="fill" style={{ width: `${pct * 100}%` }} /><div className="limit" style={{ left: '50%' }} /></div>
      <div className="sub">a wheelchair needs {limit} mm</div>
    </div>
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

function Pad({ drive, telem }: { drive: ReturnType<typeof useDrive>; telem: Telem | null }) {
  const b = (d: Dir, text: string) => (
    <button onPointerDown={() => drive.press(d)} onPointerUp={() => drive.release(d)} onPointerLeave={() => drive.release(d)}
      onContextMenu={(e) => e.preventDefault()}>{text}</button>
  );
  return (
    <div>
      <div className="pad">
        <span />{b('f', '▲')}<span />
        {b('l', '◀')}<button className="estop" onClick={drive.stopAll}>STOP</button>{b('r', '▶')}
        <span />{b('b', '▼')}<span />
      </div>
      <p className="muted small">WASD or arrows to drive, space is E-STOP. Driving cancels ROAM.<br />
        sending v {drive.vec.v.toFixed(2)} w {drive.vec.w.toFixed(2)} · scout v {telem?.v.toFixed(2) ?? '--'} w {telem?.w.toFixed(2) ?? '--'} · {telem?.mode ?? '--'}</p>
    </div>
  );
}
