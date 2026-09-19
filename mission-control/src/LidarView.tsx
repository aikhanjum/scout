import { useEffect, useRef, useState } from 'react';
import { useStore } from './store';
import type { Evidence, Scan, ScanGap } from './protocol';

// Top-down view of one lidar rotation (PROTOCOL.md section 5, the scan frame).
// Scout's frame: 0 degrees is straight ahead, positive is left. On screen ahead is up.
// Drawing only. Nothing here feeds the audit; width events come from telem.width_mm.

const COLOUR: Record<Evidence, string> = {
  see_through: '#3ddc84',   // something was seen through it: really open
  unverified: '#ffb020',    // an arc of no-returns: opening or dead surface, cannot tell
  step: '#8b98a5',          // a range step between neighbouring samples
};
const LABEL: Record<Evidence, string> = {
  see_through: 'open (saw through it)',
  unverified: 'no returns (unverified)',
  step: 'range step',
};
const RANGES = [500, 1000, 2000, 4000, 8000, 12000];

// screen position of a Scout bearing at range mm
const project = (cx: number, cy: number, pxPerMm: number, deg: number, mm: number) => {
  const th = (deg * Math.PI) / 180;
  return [cx - Math.sin(th) * mm * pxPerMm, cy - Math.cos(th) * mm * pxPerMm] as const;
};

// Frame the room, not the outliers. A handful of long sight lines down a corridor
// would otherwise squash everything nearby into a dot, so scale on the median
// return, which is whatever surface Scout is actually surrounded by.
function autoRange(scan: Scan | null) {
  const valid = (scan?.pts ?? []).filter((p) => p[1] > 0).map((p) => p[1]).sort((a, b) => a - b);
  if (!valid.length) return 2000;
  const median = valid[Math.floor(valid.length / 2)];
  return RANGES.find((r) => r >= median * 2.5) ?? RANGES[RANGES.length - 1];
}

export function LidarView() {
  const { scan, scale, link, source } = useStore();
  const [manual, setManual] = useState<number | 'auto'>('auto');
  const [showLabels, setShowLabels] = useState(true);
  const ref = useRef<HTMLCanvasElement>(null);
  const range = manual === 'auto' ? autoRange(scan) : manual;
  const stale = source.kind === 'live' && link !== 'up';

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    const g = cv.getContext('2d');
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);

    const cx = w / 2, cy = h / 2;
    const pxPerMm = (Math.min(w, h) / 2 - 18) / range;

    // range rings
    g.font = '11px system-ui, sans-serif';
    for (const r of RANGES) {
      if (r > range) break;
      g.beginPath();
      g.arc(cx, cy, r * pxPerMm, 0, Math.PI * 2);
      g.strokeStyle = '#243040'; g.lineWidth = 1; g.stroke();
      g.fillStyle = '#8b98a5';
      g.fillText(r >= 1000 ? `${r / 1000} m` : `${r} mm`, cx + 6, cy - r * pxPerMm - 4);
    }
    // cross hairs and the nose
    g.strokeStyle = '#243040';
    g.beginPath(); g.moveTo(cx - w / 2, cy); g.lineTo(cx + w / 2, cy);
    g.moveTo(cx, cy - h / 2); g.lineTo(cx, cy + h / 2); g.stroke();
    g.fillStyle = '#8b98a5';
    g.fillText('ahead', cx + 7, 12);
    g.fillText('left', 4, cy - 5);
    g.fillText('right', w - 30, cy - 5);

    if (!scan) {
      g.fillStyle = '#8b98a5'; g.font = '14px system-ui, sans-serif';
      g.fillText('no lidar', cx - 26, cy + 4);
      return;
    }

    // directions that came back with no return, drawn faintly at full range so a
    // blind arc is visible as blindness rather than as empty space
    g.strokeStyle = 'rgba(255,176,32,0.13)'; g.lineWidth = 1;
    g.beginPath();
    for (const [a, mm] of scan.pts) {
      if (mm > 0) continue;
      const [x0, y0] = project(cx, cy, pxPerMm, a, range * 0.93);
      const [x1, y1] = project(cx, cy, pxPerMm, a, range);
      g.moveTo(x0, y0); g.lineTo(x1, y1);
    }
    g.stroke();

    // the outline: join neighbouring returns that are close enough to be one surface.
    // Returns past the current range are left out entirely rather than drawn off the
    // edge, which would trail long lines across the view.
    g.strokeStyle = '#4da3ff'; g.lineWidth = 1.5;
    const valid = scan.pts.filter((p) => p[1] > 0 && p[1] <= range);
    g.beginPath();
    let pen = false;
    for (let i = 0; i < valid.length; i++) {
      const [a, mm] = valid[i];
      const [x, y] = project(cx, cy, pxPerMm, a, mm);
      const prev = i > 0 ? valid[i - 1] : null;
      const near = prev && Math.abs(a - prev[0]) < 6 && Math.abs(mm - prev[1]) < 200;
      if (near && pen) g.lineTo(x, y); else { g.moveTo(x, y); pen = true; }
    }
    g.stroke();

    // the returns themselves
    g.fillStyle = '#e6edf3';
    for (const [a, mm] of valid) {
      if (mm <= 0) continue;
      const [x, y] = project(cx, cy, pxPerMm, a, mm);
      g.fillRect(x - 1, y - 1, 2, 2);
    }

    // gaps: a chord between the two edges, coloured by how well evidenced it is
    // label only the widest few, and only ones big enough on screen to read
    const labelled = [...scan.gaps]
      .filter((g) => g.evidence !== 'step' && g.mm0 <= range && g.mm1 <= range
        && Math.max(g.mm0, g.mm1) * pxPerMm > 26)
      .sort((p, q) => q.width_mm - p.width_mm)
      .slice(0, 4);
    for (const gap of scan.gaps) {
      if (gap.mm0 > range || gap.mm1 > range) continue;
      const [x0, y0] = project(cx, cy, pxPerMm, gap.a0, gap.mm0);
      const [x1, y1] = project(cx, cy, pxPerMm, gap.a1, gap.mm1);
      g.strokeStyle = COLOUR[gap.evidence];
      g.lineWidth = gap.evidence === 'step' ? 1 : 2.5;
      g.setLineDash(gap.evidence === 'unverified' ? [5, 4] : []);
      g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke();
      g.setLineDash([]);
      if (showLabels && labelled.includes(gap)) {
        // nudge the label outward along the bearing so it clears the outline
        const mid = ((gap.a0 + gap.a1) / 2) * (Math.PI / 180);
        const mx = (x0 + x1) / 2 - Math.sin(mid) * 16;
        const my = (y0 + y1) / 2 - Math.cos(mid) * 16;
        const text = scale !== 1
          ? `${gap.width_mm} mm (${Math.round(gap.width_mm / scale / 10)} cm full)`
          : `${gap.width_mm} mm`;
        g.font = '12px system-ui, sans-serif';
        const tw = g.measureText(text).width;
        g.fillStyle = 'rgba(11,14,20,0.82)';
        g.fillRect(mx - tw / 2 - 4, my - 9, tw + 8, 16);
        g.fillStyle = COLOUR[gap.evidence];
        g.fillText(text, mx - tw / 2, my + 3);
      }
    }
  }, [scan, range, scale, showLabels]);

  const counts = countByEvidence(scan?.gaps ?? []);
  return (
    <div className={`lidar ${stale ? 'stale' : ''}`}>
      <div className="lidar-head">
        <span>LIDAR</span>
        <span className="muted small">
          {scan ? `${scan.pts.length} pts · ${scan.hz} Hz · ${scan.mode}` : 'waiting for a rotation'}
        </span>
        <span className="spacer" />
        <select value={String(manual)} onChange={(e) => setManual(e.target.value === 'auto' ? 'auto' : Number(e.target.value))}>
          <option value="auto">auto range</option>
          {RANGES.map((r) => <option key={r} value={r}>{r >= 1000 ? `${r / 1000} m` : `${r} mm`}</option>)}
        </select>
        <button className={showLabels ? 'on' : ''} onClick={() => setShowLabels(!showLabels)}>mm</button>
      </div>
      <canvas ref={ref} className="lidar-canvas" />
      <div className="lidar-legend small">
        {(['see_through', 'unverified', 'step'] as Evidence[]).map((e) => (
          <span key={e}><i style={{ background: COLOUR[e] }} />{LABEL[e]} {counts[e] ? `· ${counts[e]}` : ''}</span>
        ))}
      </div>
      {counts.unverified > 0 && (
        <p className="muted small lidar-note">
          Dashed gaps are arcs where nothing came back. One rotation cannot tell an opening
          from glass, a mirror or matte black, so these are candidates, not measurements.
        </p>
      )}
    </div>
  );
}

function countByEvidence(gaps: ScanGap[]) {
  return gaps.reduce((acc, g) => ({ ...acc, [g.evidence]: (acc[g.evidence] ?? 0) + 1 }),
    { see_through: 0, unverified: 0, step: 0 } as Record<Evidence, number>);
}
