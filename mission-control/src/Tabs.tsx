import { useEffect, useRef, useState } from 'react';
import { setView, useView, type View } from './App';
import { send } from './scout';
import './nav.css';

// Bloomberg-style navigation: every screen has a mnemonic and a number key, and a command line
// where a code plus Enter (GO) does the same, or drives the robot. Digits switch screens from
// anywhere except a text field; "/" focuses the command line.
const SCREENS: [View, string, string][] = [['terminal', 'TERM', 'terminal'], ['map', 'MAP', 'inferred position'], ['tiger', 'TIGR', 'tiger data'], ['eye', 'EYES', "scout's eyes"]];
const ALIAS: Record<string, View> = { term: 'terminal', terminal: 'terminal', map: 'map', pos: 'map', tigr: 'tiger', tiger: 'tiger', eyes: 'eye', eye: 'eye' };
const HELP = 'TERM MAP TIGR EYES · ROAM TELE STOP MARK';

function run(raw: string): [string, boolean] {
  const [code, ...rest] = raw.trim().toLowerCase().split(/\s+/);
  if (!code) return ['', false];
  if (/^[1-4]$/.test(code)) { setView(SCREENS[Number(code) - 1][0]); return [SCREENS[Number(code) - 1][2], false]; }
  if (code in ALIAS) { setView(ALIAS[code]); return [SCREENS.find((s) => s[0] === ALIAS[code])![2], false]; }
  switch (code) {
    case 'roam': send({ cmd: 'mode', mode: 'wall_follow' }); return ['roam: wall_follow sent', false];
    case 'tele': case 'teleop': send({ cmd: 'mode', mode: 'teleop' }); return ['teleop sent', false];
    case 'stop': case 'estop': send({ cmd: 'stop' }); return ['E-STOP sent', false];
    case 'mark': send({ cmd: 'mark', label: rest.join(' ') }); return ['mark sent', false];
    case 'help': case '?': return [HELP, false];
  }
  return [`unknown ${code.toUpperCase()} · ${HELP}`, true];
}

export function Tabs() {
  const view = useView();
  const [text, setText] = useState('');
  const [msg, setMsg] = useState<[string, boolean]>(['', false]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!msg[0]) return;
    const t = setTimeout(() => setMsg(['', false]), 4000);
    return () => clearTimeout(t);
  }, [msg]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT');
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (!typing && /^[1-4]$/.test(e.key)) { setView(SCREENS[Number(e.key) - 1][0]); e.preventDefault(); }
      else if (!typing && e.key === '/') { input.current?.focus(); e.preventDefault(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const go = () => { setMsg(run(text)); setText(''); };

  return (
    <nav className="cmdbar" aria-label="navigation">
      {SCREENS.map(([v, code], i) => (
        <button key={v} className={v === view ? 'mn on' : 'mn'} onClick={() => setView(v)} title={SCREENS[i][2]}><b>{i + 1}</b>{code}</button>
      ))}
      <span className="cmd">
        <span className="p">&gt;</span>
        <input ref={input} value={text} placeholder="code, then GO  (/ to type)" spellCheck={false}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') go(); else if (e.key === 'Escape') { setText(''); input.current?.blur(); } e.stopPropagation(); }} />
      </span>
      <button className="go" onClick={go}>GO</button>
      {msg[0] && <span className={msg[1] ? 'msg err' : 'msg'}>{msg[0]}</span>}
    </nav>
  );
}
