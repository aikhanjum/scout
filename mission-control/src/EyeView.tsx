// OWNER: eye agent. The phone screen: a judge scans the QR code and gets Scout's eyes full-screen
// with the clearance number under them. Read-only: no drive, no source picker. It connects by itself.
import { useEffect, useState } from 'react';
import { ScoutEye } from './ScoutEye';
import { Tabs } from './Tabs';
import { useStore, type Source } from './store';
import { connect } from './scout';
import { widthRule } from './protocol';

const fmt = (n: number) => Math.round(n).toLocaleString('en-US');

// ?run=<file> replays data/runs/<file>, the way the SOURCE menu does. ?ws=<url> is a dev override
// for a Scout at another address (a second fake robot, a Pi by IP). Neither is persisted.
function urlSource(): Source | null {
  const q = new URLSearchParams(location.search);
  const run = q.get('run');
  if (run) return { kind: 'replay', name: run };
  const ws = q.get('ws');
  if (ws) return { kind: 'live', url: ws };
  return null;
}

const same = (a: Source, b: Source) =>
  a.kind === 'live' ? b.kind === 'live' && a.url === b.url : b.kind === 'replay' && a.name === b.name;

let booted = false;   // the first mount after a page load connects; a later tab switch keeps the link and the board

export function EyeView() {
  const [fov, setFov] = useState(120);
  const telem = useStore((s) => s.telem);
  const link = useStore((s) => s.link);
  const fw = useStore((s) => s.fw);
  const source = useStore((s) => s.source);
  const rules = useStore((s) => s.rules);

  useEffect(() => {
    const want = urlSource();
    if (!want) {
      if (!booted) { booted = true; connect(useStore.getState().source); }
      return;
    }
    connect(want);
    // main.tsx connects to the default live source once rules.json has loaded, which can land after
    // this mount and would replace what the URL asked for. Put it back, the one time that happens.
    return useStore.subscribe((s, prev) => { if (s.source !== prev.source && !same(s.source, want)) connect(want); });
  }, []);

  const limit = widthRule(rules)?.limit ?? 860;
  const replay = source.kind === 'replay';
  const simulated = fw.startsWith('fake');
  const down = !replay && link !== 'up';
  const tag = replay && simulated ? ['SIMULATED REPLAY', 'amber']
    : replay ? ['REPLAY', 'blue']
    : down ? ['LINK DOWN', 'red']
    : simulated ? ['SIMULATED', 'amber']
    : ['LIVE', 'green'];

  const mm = telem?.lidar ? telem.clearance_mm : 0;
  const clear = mm > 0 ? [mm >= limit ? `CLEAR ${fmt(mm)} mm` : `NARROW ${fmt(mm)} mm`, mm >= limit ? 'green' : 'red']
    : ['NO MEASUREMENT', 'dim'];

  return (
    <div className="eye-page" onClick={() => setFov((f) => (f === 120 ? 360 : 120))}>
      <header className="eye-top">
        <span className="eye-title">SCOUT · EYES</span>
        <span className="eye-fov">FOV {fov}</span>
        <span className="eye-space" />
        <span onClick={(e) => e.stopPropagation()}><Tabs /></span>
        <span className={`eye-tag ${tag[1]}`}>{tag[0]}</span>
      </header>
      <div className="eye-main"><ScoutEye fov={fov} /></div>
      <footer className="eye-bottom">
        <div className={`eye-clear ${clear[1]}${down && telem ? ' stale' : ''}`}>{clear[0]}</div>
        <div className="eye-note">what the lidar sees, 10 frames a second, nothing inferred</div>
      </footer>
    </div>
  );
}
