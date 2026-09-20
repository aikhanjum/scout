// OWNER: eye agent. First-person render of telem.scan (360 ranges, mm) for the front field of view.
import { useStore } from './store';
export function ScoutEye({ fov = 120 }: { fov?: number }) {
  const telem = useStore((s) => s.telem);
  return <div className="stub">SCOUT'S EYES stub · fov {fov} · returns {telem?.scan.filter((r) => r > 0).length ?? 0}</div>;
}
