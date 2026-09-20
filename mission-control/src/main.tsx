import { Component, StrictMode, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { useStore } from './store';
import { connect } from './scout';
import './style.css';

// A render error must never blank the big screen mid-demo.
class Boundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: unknown) { return { error: String(e) }; }
  render() {
    if (this.state.error) return <div className="crash" role="alert"><span>Dashboard error: {this.state.error}</span> <button onClick={() => location.reload()}>Reload</button></div>;
    return this.props.children;
  }
}

async function loadJson<T>(path: string): Promise<T | null> {
  try { return await (await fetch(path)).json(); } catch { return null; }
}

const DEFAULT_URL = 'ws://localhost:8080/ws';

(async () => {
  useStore.getState().set({ rules: await loadJson<never>('/rules.json') });
  let url = DEFAULT_URL;
  try { url = localStorage.getItem('scout.url') ?? DEFAULT_URL; } catch { /* private window etc. */ }
  // ?run=<file> replays data/runs/<file> on any screen; ?ws=<url> picks a live robot. A QR code
  // or a rehearsal link can carry either.
  const q = new URLSearchParams(location.search);
  const run = q.get('run');
  const ws = q.get('ws');
  if (ws) try { localStorage.setItem('scout.url', ws); } catch { /* fine */ }   // a reload without ?ws= keeps the robot
  if (run) connect({ kind: 'replay', name: run });
  else connect({ kind: 'live', url: ws ?? url });
})();

createRoot(document.getElementById('root')!).render(
  <StrictMode><Boundary><App /></Boundary></StrictMode>,
);
