import { useEffect, useRef, useState } from 'react';
import { useStore } from './store';
import { send, sendConfig, startRecording, stopRecording } from './scout';
import { ruleFor, type Telem } from './protocol';
import { verdict } from './verdict';

const SPEED = 0.5, TURN = 0.5;
type Dir = 'f' | 'b' | 'l' | 'r';
const KEYS: Record<string, Dir> = { w: 'f', arrowup: 'f', s: 'b', arrowdown: 'b', a: 'l', arrowleft: 'l', d: 'r', arrowright: 'r' };

export function Live() {
  const { telem, events, link, source, scale, tableMode, rules, recording, set } = useStore();
  const [space, setSpace] = useState('Table course');
  const [mark, setMark] = useState('');
  const drive = useDrive();

  const slopeLimit = ruleFor(rules, 'slope')?.limit ?? 4.76;
  const widthLimit = (ruleFor(rules, 'width')?.limit ?? 860) * scale;
  const imuOk = telem?.imu !== false, lidarOk = telem?.lidar !== false;
  const pitch = telem && imuOk ? Math.abs(telem.pitch_deg) : null;
  const width = telem && lidarOk && telem.width_mm > 0 ? telem.width_mm : null;
  const stale = source.kind === 'live' && link !== 'up';
  const cm = (mm: number) => Math.round(mm / scale / 10);

  const toggleTable = () => { set({ tableMode: !tableMode }); sendConfig(); };
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
        <button className={tableMode ? 'on' : ''} onClick={toggleTable}>TABLE MODE {tableMode ? 'ON' : 'OFF'}</button>
        {scale !== 1 && <span className="badge">Scale course 1:{Math.round(1 / scale)}</span>}
        <span className="spacer" />
        <input value={space} onChange={(e) => setSpace(e.target.value)} placeholder="space name" size={14} />
        <button onClick={() => send({ cmd: 'run', action: 'start', space })}>Start run</button>
        <button onClick={() => send({ cmd: 'run', action: 'stop' })}>Stop run</button>
        <input value={mark} onChange={(e) => setMark(e.target.value)} placeholder="mark label" size={10} />
        <button onClick={() => send({ cmd: 'mark', label: mark || 'mark' })}>MARK</button>
        <button onClick={() => send({ cmd: 'zero' })}>ZERO</button>
        <button onClick={() => send({ cmd: 'beep' })}>BEEP</button>
        <button className={recording ? 'on' : ''} onClick={toggleRec}>{recording ? '■ Save recording' : '● REC'}</button>
      </div>

      {stale && <div className="stale-note">LINK DOWN. Last known values, not live.</div>}
      <div className={stale ? 'stale' : ''}>
        <div className="gauges">
          <Gauge label="SLOPE" value={pitch?.toFixed(1)} unit="°"
            pct={pitch == null ? 0 : pitch / 12} limitPct={slopeLimit / 12}
            bad={pitch != null && pitch > slopeLimit}
            tag={telem?.measuring ? 'MEASURING' : telem && !imuOk ? 'NO IMU' : undefined}
            sub={`limit ${slopeLimit}°`} />
          <Gauge label="WIDTH" value={width == null ? undefined : String(width)} unit="mm"
            pct={width == null ? 0 : width / (2 * widthLimit)} limitPct={0.5}
            bad={width != null && width < widthLimit}
            tag={telem && !lidarOk ? 'NO LIDAR' : undefined}
            sub={scale !== 1
              ? `${width == null ? '--' : cm(width)} cm at full scale · limit ${Math.round(widthLimit)} mm (${cm(widthLimit)} cm)`
              : `limit ${Math.round(widthLimit)} mm`} />
        </div>
        <div className="bottom">
          <Pad drive={drive} telem={telem} />
          <ul className="feed">
            {events.length === 0 && <li className="info"><span className="kind">·</span><span className="muted">No events yet</span><span /></li>}
            {events.map((e) => (
              <li key={e.id} className={e.kind.endsWith('fail') ? 'fail' : e.kind.endsWith('pass') ? 'pass' : e.kind === 'mark' ? 'mark' : 'info'}>
                <span className="kind">{e.kind}</span><span>{verdict(e)}</span><span className="t">{(e.t / 1000).toFixed(1)}s</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}

function Gauge(p: { label: string; value?: string; unit: string; pct: number; limitPct: number; bad: boolean; tag?: string; sub: string }) {
  const w = (x: number) => `${Math.max(0, Math.min(1, x)) * 100}%`;
  return (
    <div className={`gauge ${p.value == null ? '' : p.bad ? 'bad' : 'good'}`}>
      <div className="gauge-head"><span>{p.label}</span>{p.tag && <span className="tag">{p.tag}</span>}</div>
      <div className="big">{p.value ?? '--'}<small>{p.unit}</small></div>
      <div className="bar"><div className="fill" style={{ width: w(p.pct) }} /><div className="limit" style={{ left: w(p.limitPct) }} /></div>
      <div className="sub">{p.sub}</div>
    </div>
  );
}

// Teleop. While any key or button is held, resend drive every 100 ms (PROTOCOL.md section 4).
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
      <p className="muted small">WASD or arrows to drive, space is E-STOP.<br />
        sending v {drive.vec.v.toFixed(2)} w {drive.vec.w.toFixed(2)} · scout v {telem?.v.toFixed(2) ?? '--'} w {telem?.w.toFixed(2) ?? '--'} · {telem?.mode ?? '--'}</p>
    </div>
  );
}
