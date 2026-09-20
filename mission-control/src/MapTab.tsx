// Quaden's blueprint screen as its own tab. Position on this map is inferred, not measured
// (CLAUDE.md: no odometry, no IMU, slam drifts), and the banner says so before the map does.
// SourceBar is mounted here because it is what drives connect().
import { Live } from './Live';
import { SourceBar } from './SourceBar';
import { Tabs } from './Tabs';
import './radar.css';

export function MapTab() {
  return (
    <div className="maptab">
      <header className="maptab-head">
        <span className="maptab-brand">SCOUT</span>
        <Tabs />
        <SourceBar />
      </header>
      <p className="maptab-banner">
        <b>INFERRED POSITION</b> — Scout has no odometry and no IMU. This map is inferred by matching each scan to
        the last (SLAM); boundaries are inferred, not measured, and drift is expected. No verdict uses this map.
      </p>
      <main className="maptab-body"><Live /></main>
    </div>
  );
}
