// OWNER: terminal agent. The DRIVE tile: a 3x3 pad, E-STOP and ROAM. The teleop logic is
// Live.tsx's useDrive, copied rather than imported (Live.tsx is read-only and the hook is private),
// with the held set mirrored into state so the keys can light up.
import { useEffect, useRef, useState } from 'react';
import { useStore } from './store';
import { send } from './scout';

const SPEED = 0.5, TURN = 0.5;
type Dir = 'f' | 'b' | 'l' | 'r';
const KEYS: Record<string, Dir> = { w: 'f', arrowup: 'f', s: 'b', arrowdown: 'b', a: 'l', arrowleft: 'l', d: 'r', arrowright: 'r' };

// Teleop. While any key or pad button is held, resend drive every 100 ms (PROTOCOL.md section 5).
// Release sends stop. Space is E-STOP. Losing window focus sends stop.
function useDrive() {
  const held = useRef(new Set<Dir>());
  const timer = useRef(0);
  const [lit, setLit] = useState<Dir[]>([]);
  const [estop, setEstop] = useState(false);
  const sync = () => setLit([...held.current]);

  const tick = () => {
    const h = held.current;
    send({ cmd: 'drive', v: ((h.has('f') ? 1 : 0) - (h.has('b') ? 1 : 0)) * SPEED, w: ((h.has('l') ? 1 : 0) - (h.has('r') ? 1 : 0)) * TURN });
  };
  const stopAll = () => {
    held.current.clear();
    clearInterval(timer.current); timer.current = 0;
    send({ cmd: 'stop' });
    sync();
  };
  const press = (d: Dir) => {
    held.current.add(d);
    if (!timer.current) { tick(); timer.current = window.setInterval(tick, 100); }
    sync();
  };
  const release = (d: Dir) => {
    if (!held.current.delete(d)) return;
    sync();
    if (held.current.size === 0) stopAll();
  };

  useEffect(() => {
    const typing = (e: KeyboardEvent) => ['INPUT', 'SELECT', 'TEXTAREA'].includes((e.target as HTMLElement)?.tagName);
    const down = (e: KeyboardEvent) => {
      if (typing(e)) return;
      if (e.key === ' ') { e.preventDefault(); if (!e.repeat) setEstop(true); stopAll(); return; }
      const d = KEYS[e.key.toLowerCase()];
      if (d) { e.preventDefault(); if (!e.repeat) press(d); }
    };
    const up = (e: KeyboardEvent) => {
      if (e.key === ' ') setEstop(false);
      const d = KEYS[e.key.toLowerCase()]; if (d) release(d);
    };
    const blur = () => { setEstop(false); if (held.current.size) stopAll(); };
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

  return { press, release, stopAll, lit, estop, setEstop };
}

const GLYPH: Record<Dir, [string, string]> = { f: ['▲', 'Forward'], b: ['▼', 'Back'], l: ['◀', 'Left'], r: ['▶', 'Right'] };

export function Drive() {
  const { press, release, stopAll, lit, estop, setEstop } = useDrive();
  const mode = useStore((s) => s.telem?.mode);
  const replay = useStore((s) => s.source.kind === 'replay');
  const roaming = mode === 'wall_follow';

  const key = (d: Dir) => (
    <button type="button" className={`key${lit.includes(d) ? ' on' : ''}`} aria-label={GLYPH[d][1]}
      onPointerDown={(e) => { e.preventDefault(); press(d); }} onPointerUp={() => release(d)}
      onPointerCancel={() => release(d)} onPointerLeave={() => release(d)} onContextMenu={(e) => e.preventDefault()}>
      {GLYPH[d][0]}
    </button>
  );

  return (
    <div className={`drive${replay ? ' off' : ''}`}>
      <div className="pad">
        <span />{key('f')}<span />
        {key('l')}<span className="mid">{lit.length ? '●' : ''}</span>{key('r')}
        <span />{key('b')}<span />
      </div>
      <div className="btns">
        <button type="button" className={`estop${estop ? ' on' : ''}`} disabled={replay}
          onPointerDown={() => { setEstop(true); stopAll(); }} onPointerUp={() => setEstop(false)}
          onPointerCancel={() => setEstop(false)} onPointerLeave={() => setEstop(false)}>E-STOP</button>
        <button type="button" className={`roam${roaming ? ' on' : ''}`} aria-pressed={roaming} disabled={replay}
          onClick={() => send({ cmd: 'mode', mode: roaming ? 'teleop' : 'wall_follow' })}>ROAM</button>
      </div>
      <div className="hint first">{replay ? 'REPLAY · keys do nothing' : 'WASD / arrows · space E-STOP'}</div>
      <div className="hint">SPEED {SPEED.toFixed(1)} · TURN {TURN.toFixed(1)}</div>
    </div>
  );
}
