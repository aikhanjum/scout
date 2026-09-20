// OWNER: terminal agent. The Bloomberg-style screen. Composes the other components; owns terminal.css.
import { SourceBar } from './SourceBar';
import { ScoutEye } from './ScoutEye';
import { Radar } from './Radar';
import { TigerPanel } from './TigerPanel';
import { QrCard } from './QrCard';
import { History } from './History';
export function Terminal() {
  return (
    <div className="stub">
      <SourceBar />
      <ScoutEye /><Radar /><TigerPanel /><QrCard /><History />
    </div>
  );
}
