import { useEffect, useState } from 'react';
import { Terminal } from './Terminal';
import { MapTab } from './MapTab';
import { TigerTab } from './TigerTab';
import { EyeView } from './EyeView';

// One URL, four screens. ?view=terminal|map|tiger|eye. A narrow screen (a phone that scanned the
// QR code) gets Scout's eyes full-screen unless it asks for something else.
export type View = 'terminal' | 'map' | 'tiger' | 'eye';

export function currentView(): View {
  const v = new URLSearchParams(location.search).get('view');
  if (v === 'terminal' || v === 'map' || v === 'tiger' || v === 'eye') return v;
  return window.matchMedia('(max-width: 700px)').matches ? 'eye' : 'terminal';
}

export function setView(v: View) {
  const u = new URL(location.href);
  u.searchParams.set('view', v);
  history.pushState(null, '', u);
  window.dispatchEvent(new Event('viewchange'));
}

export function useView(): View {
  const [v, setV] = useState<View>(currentView);
  useEffect(() => {
    const on = () => setV(currentView());
    window.addEventListener('popstate', on);
    window.addEventListener('viewchange', on);
    return () => { window.removeEventListener('popstate', on); window.removeEventListener('viewchange', on); };
  }, []);
  return v;
}

export function App() {
  const view = useView();
  // Lets each screen's stylesheet set the page background: html[data-view="terminal"] body { ... }
  useEffect(() => { document.documentElement.dataset.view = view; }, [view]);
  if (view === 'eye') return <EyeView />;
  if (view === 'map') return <MapTab />;
  if (view === 'tiger') return <TigerTab />;
  return <Terminal />;
}
