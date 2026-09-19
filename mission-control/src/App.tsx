import { useEffect, useState } from 'react';
import { useStore } from './store';
import { connect } from './scout';
import { Live } from './Live';
import { Spaces } from './Spaces';

const DEFAULT_URL = 'ws://localhost:8080/ws';

export function App() {
  const [tab, setTab] = useState<'live' | 'spaces'>('live');
  return (
    <>
      <header>
        <h1>SCOUT</h1>
        <nav className="tabs">
          <button className={tab === 'live' ? 'active' : ''} onClick={() => setTab('live')}>LIVE</button>
          <button className={tab === 'spaces' ? 'active' : ''} onClick={() => setTab('spaces')}>SPACES</button>
        </nav>
        <SourceBar />
      </header>
      <main>{tab === 'live' ? <Live /> : <Spaces />}</main>
    </>
  );
}

// Where frames come from. Always visible so it can be swapped mid-demo: a live Scout, or a recorded run.
function SourceBar() {
  const { source, link, detail, voice, set } = useStore();
  const [url, setUrl] = useState(() => { try { return localStorage.getItem('scout.url') ?? DEFAULT_URL; } catch { return DEFAULT_URL; } });
  const [runs, setRuns] = useState<string[]>([]);
  const [run, setRun] = useState('');
  useEffect(() => {
    fetch('/runs/index.json').then((r) => r.json())
      .then((list: string[]) => { setRuns(list); setRun(list[0] ?? ''); })
      .catch(() => setRuns([]));
  }, []);
  const goLive = () => { try { localStorage.setItem('scout.url', url); } catch { /* fine */ } connect({ kind: 'live', url }); };
  const state = source.kind === 'replay' ? 'replay' : link;
  const label = source.kind === 'replay' ? 'REPLAY' : link === 'up' ? 'LINK UP' : 'LINK DOWN';
  return (
    <div className="sourcebar">
      <span className={`link ${state}`}>{label}</span>
      <span className="muted detail">{detail}</span>
      <span className="spacer" />
      <label>Live <input list="urls" value={url} onChange={(e) => setUrl(e.target.value)} size={24} /></label>
      <datalist id="urls">
        <option value={DEFAULT_URL} /><option value="ws://scout.local:8080/ws" />
      </datalist>
      <button onClick={goLive}>Connect</button>
      <label>Replay <select value={run} onChange={(e) => setRun(e.target.value)}>{runs.map((r) => <option key={r}>{r}</option>)}</select></label>
      <button disabled={!run} onClick={() => connect({ kind: 'replay', name: run })}>Play</button>
      <label className="filepick">or file <input type="file" accept=".ndjson,.txt"
        onChange={async (e) => { const f = e.target.files?.[0]; if (f) connect({ kind: 'replay', name: f.name, text: await f.text() }); }} /></label>
      <label><input type="checkbox" checked={voice} onChange={(e) => set({ voice: e.target.checked })} /> Voice</label>
    </div>
  );
}
