import { useEffect, useRef, useState } from 'react';
import { useStore } from './store';
import { connect } from './scout';

const DEFAULT_URL = 'ws://localhost:8080/ws';
const LIVE: [string, string][] = [[DEFAULT_URL, 'Live: this laptop'], ['ws://scout.local:8080/ws', 'Live: scout.local']];

// Where frames come from: one picker, always visible so it can be swapped mid-demo.
// A live Scout (two known addresses or a typed one), a recorded run, or a file from disk.
export function SourceBar() {
  const { source, link, detail, voice, set } = useStore();
  const [runs, setRuns] = useState<string[]>([]);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    fetch('/runs/index.json').then((r) => r.json()).then((list: string[]) => setRuns(list)).catch(() => setRuns([]));
  }, []);

  const current = source.kind === 'live' ? source.url : `run:${source.name}`;
  const known = source.kind === 'live' ? LIVE.some(([u]) => u === source.url) : runs.includes(source.name);
  const pick = (v: string) => {
    if (v === 'custom') {
      const url = window.prompt('Scout WebSocket URL', source.kind === 'live' ? source.url : DEFAULT_URL)?.trim();
      if (!url) return;
      try { localStorage.setItem('scout.url', url); } catch { /* fine */ }
      connect({ kind: 'live', url });
    } else if (v === 'file') {
      file.current?.click();
    } else if (v.startsWith('run:')) {
      connect({ kind: 'replay', name: v.slice(4) });
    } else {
      try { localStorage.setItem('scout.url', v); } catch { /* fine */ }
      connect({ kind: 'live', url: v });
    }
  };

  // Only what is wrong, or worth saying out loud on stage. A healthy live link shows nothing:
  // a dropped one raises the alert over the map, which is louder than a badge that is always there.
  const badge = source.kind === 'replay' ? { text: 'Replay', cls: 'warn' }
    : link === 'up' ? null : { text: 'Link down', cls: 'bad live' };
  return (
    <>
      <span className="status" role="status">
        {badge && <span className={`tag ${badge.cls}`}>{badge.text}</span>}
        {detail && <span className="detail" title={detail}>{detail}</span>}
      </span>
      <div className="source">
        <label className="field">Source
          <select value={current} onChange={(e) => { pick(e.target.value); e.target.blur(); }} name="source">
            <optgroup label="Live">
              {LIVE.map(([u, name]) => <option key={u} value={u}>{name}</option>)}
              {source.kind === 'live' && !known && <option value={source.url}>Live: {source.url}</option>}
              <option value="custom">Live: other address…</option>
            </optgroup>
            <optgroup label="Replay">
              {runs.map((r) => <option key={r} value={`run:${r}`}>{r}</option>)}
              {source.kind === 'replay' && !known && <option value={`run:${source.name}`}>{source.name}</option>}
              <option value="file">Open a file…</option>
            </optgroup>
          </select>
        </label>
        <input ref={file} type="file" accept=".ndjson,.txt" name="replay-file" hidden
          onChange={async (e) => { const f = e.target.files?.[0]; if (f) connect({ kind: 'replay', name: f.name, text: await f.text() }); e.target.value = ''; }} />
        <label className="field check"><input type="checkbox" name="voice" checked={voice} onChange={(e) => set({ voice: e.target.checked })} /> Voice</label>
      </div>
    </>
  );
}
