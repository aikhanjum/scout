// The top-down lidar ring for the terminal: telem.scan as one dot per degree, gaps as arcs on the
// rim, the last 15 rotations fading behind the live one. Geometry follows LidarView.tsx: bearing 0
// is straight ahead, positive is left, ahead is up on screen. Drawing only; no verdict reads this.
import { useEffect, useMemo, useRef, useState } from 'react';
import { useStore } from './store';
import { C, distanceColour } from './palette';
import type { Evidence, Telem } from './protocol';
import './radar.css';

const RANGES = [2000, 4000, 6000, 8000];   // mm: the rim snaps to one of these
const MARGIN = 24;                          // px outside the rim, room for the gap labels
const FADE = 0.8;                           // trail alpha per frame: 15 frames at 10 Hz is 1.5 s
const JUMP = 300;                           // mm: a bigger step between neighbours is not one surface
const LABELS = 4;                           // gap labels per evidence kind: the widest few, or nothing reads
const EVIDENCE: Record<Evidence, string> = { see_through: C.green, unverified: C.amber, step: C.dim };

const fit = (mm: number) => RANGES.find((r) => r >= mm) ?? RANGES[RANGES.length - 1];

// Grows as soon as the 95th percentile spills past the rim; shrinks only when it would fit the
// smaller ring with 20% to spare, so a scan hovering near a boundary does not flicker.
function pickRange(p95: number, current: number) {
  if (p95 <= 0) return current;
  const up = fit(p95);
  if (up > current) return up;
  const down = fit(p95 * 1.2);
  return down < current ? down : current;
}

type Stats = { returns: number; p95: number; nearestMm: number; nearestDeg: number; counts: Record<Evidence, number> };

function measure(telem: Telem | null): Stats | null {
  if (!telem?.lidar || telem.scan.length !== 360) return null;
  const valid: number[] = [];
  let nearestMm = 0, nearestDeg = 0;
  for (let i = 0; i < 360; i++) {
    const mm = telem.scan[i];
    if (mm <= 0) continue;
    valid.push(mm);
    if (!nearestMm || mm < nearestMm) { nearestMm = mm; nearestDeg = i; }
  }
  valid.sort((a, b) => a - b);
  const p95 = valid.length ? valid[Math.min(valid.length - 1, Math.floor(valid.length * 0.95))] : 0;
  const counts: Record<Evidence, number> = { see_through: 0, unverified: 0, step: 0 };
  for (const g of telem.gaps ?? []) if (g.evidence in counts) counts[g.evidence] += 1;
  return { returns: valid.length, p95, nearestMm, nearestDeg, counts };
}

// L37 is 37 degrees to the left, R12 to the right
const bearing = (deg: number) => {
  const s = deg > 180 ? deg - 360 : deg;
  return s === 0 ? 'AHEAD' : s === 180 ? 'BEHIND' : s > 0 ? `L${s}` : `R${-s}`;
};

const gapsText = (c: Record<Evidence, number>) => {
  const parts: string[] = [];
  if (c.see_through) parts.push(`${c.see_through} SEE-THROUGH`);
  if (c.unverified) parts.push(`${c.unverified} ?`);
  return parts.length ? parts.join(' · ') : 'NO GAPS';
};

// canvas angle of a bearing: ahead is up, left is left, and canvas angles grow clockwise
const angle = (deg: number) => -(Math.PI / 2 + (deg * Math.PI) / 180);

// One frame. `advance` is a new rotation (the trail fades a step and takes this scan's dots);
// a resize or a link change redraws without touching the trail. `reset` empties the trail
// first, which a change of range needs because the trail is in screen pixels.
function draw(cv: HTMLCanvasElement, trail: HTMLCanvasElement, side: number, telem: Telem | null,
  range: number, alpha: number, advance: boolean, reset: boolean) {
  const dpr = window.devicePixelRatio || 1;
  const px = Math.round(side * dpr);
  if (cv.width !== px || cv.height !== px) { cv.width = px; cv.height = px; }
  if (trail.width !== px || trail.height !== px) { trail.width = px; trail.height = px; }   // also clears it
  const g = cv.getContext('2d'), t = trail.getContext('2d');
  if (!g || !t) return;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  t.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (reset) t.clearRect(0, 0, side, side);
  const c = side / 2, rim = c - MARGIN, k = rim / range;
  const at = (deg: number, mm: number): [number, number] => {
    const b = (deg * Math.PI) / 180;
    return [c - Math.sin(b) * mm * k, c - Math.cos(b) * mm * k];
  };

  g.globalAlpha = 1;
  g.fillStyle = C.panel; g.fillRect(0, 0, side, side);
  g.font = `11px ${getComputedStyle(cv).fontFamily}`;   // the tile's font: var(--mono) through radar.css
  g.textAlign = 'left'; g.textBaseline = 'alphabetic'; g.lineWidth = 1;

  // the clearance sector (45 either side) and the front cone (25) as faint spokes
  const spokes: [number, string][] = [[45, 'rgba(215,221,229,0.16)'], [-45, 'rgba(215,221,229,0.16)'], [25, 'rgba(215,221,229,0.08)'], [-25, 'rgba(215,221,229,0.08)']];
  for (const [deg, colour] of spokes) {
    const [x, y] = at(deg, range);
    g.strokeStyle = colour; g.beginPath(); g.moveTo(c, c); g.lineTo(x, y); g.stroke();
  }
  // a ring every metre, labelled at its top
  g.strokeStyle = C.rule; g.fillStyle = C.dim;
  for (let r = 1000; r <= range; r += 1000) {
    g.beginPath(); g.arc(c, c, r * k, 0, Math.PI * 2); g.stroke();
    g.fillText(`${r / 1000} m`, c + 4, c - r * k - 3);
  }

  const scan = telem && telem.lidar && telem.scan.length === 360 ? telem.scan : null;
  if (telem && scan) {
    if (advance) {   // everything already in the trail fades one step
      t.globalCompositeOperation = 'destination-in';
      t.fillStyle = `rgba(0,0,0,${FADE})`; t.fillRect(0, 0, side, side);
      t.globalCompositeOperation = 'source-over';
    }
    g.globalAlpha = alpha;
    g.drawImage(trail, 0, 0, side, side);

    // walls read as walls: neighbours are joined when both returned and the step between them is small
    g.strokeStyle = 'rgba(138,147,158,0.55)'; g.beginPath();
    for (let i = 0; i < 360; i++) {
      const a = scan[i], b = scan[(i + 1) % 360];
      if (a <= 0 || b <= 0 || a > range || b > range || Math.abs(a - b) > JUMP) continue;
      const [x0, y0] = at(i, a), [x1, y1] = at(i + 1, b);
      g.moveTo(x0, y0); g.lineTo(x1, y1);
    }
    g.stroke();
    // one dot per degree coloured by distance; the same dot goes into the trail for next time
    for (let i = 0; i < 360; i++) {
      const mm = scan[i];
      if (mm <= 0 || mm > range) continue;
      const [x, y] = at(i, mm);
      const colour = distanceColour(mm);
      g.fillStyle = colour; g.fillRect(x - 1, y - 1, 2, 2);
      if (advance) { t.fillStyle = colour; t.fillRect(x - 1, y - 1, 2, 2); }
    }
    // gaps: an arc on the rim between the two edges, coloured by evidence. Only a see-through gap
    // carries a width; an unverified one is an arc of no-returns and gets a question mark. A real
    // rotation finds dozens, so only the widest few of each are labelled; every arc is still drawn
    const gaps = telem.gaps ?? [];
    const widths = gaps.filter((x) => x.evidence === 'see_through').sort((a, b) => b.width_mm - a.width_mm).slice(0, LABELS);
    const marks = gaps.filter((x) => x.evidence === 'unverified').sort((a, b) => b.span_deg - a.span_deg).slice(0, LABELS);
    g.textAlign = 'center';
    for (const gap of gaps) {
      const sweep = (((gap.a1 - gap.a0) % 360) + 360) % 360;   // degrees, counter-clockwise from a0
      if (sweep <= 0) continue;
      const colour = EVIDENCE[gap.evidence] ?? C.dim;
      g.strokeStyle = colour; g.lineWidth = gap.evidence === 'step' ? 1.5 : 3;
      g.beginPath(); g.arc(c, c, rim + 2, angle(gap.a0), angle(gap.a0 + sweep), true); g.stroke();
      const label = widths.includes(gap) ? `${gap.width_mm} mm` : marks.includes(gap) ? '?' : null;
      if (!label) continue;
      const [lx, ly] = at(gap.a0 + sweep / 2, range + 13 / k);
      const half = g.measureText(label).width / 2 + 2;   // kept inside the square: a gap at 90 degrees sits on the edge
      g.fillStyle = colour; g.fillText(label, Math.max(half, Math.min(side - half, lx)), Math.max(12, Math.min(side - 4, ly + 4)));
    }
    g.lineWidth = 1; g.textAlign = 'left';
  }
  // Scout, pointing up
  g.globalAlpha = 1; g.fillStyle = C.fg;
  g.beginPath(); g.moveTo(c, c - 7); g.lineTo(c - 5, c + 5); g.lineTo(c + 5, c + 5); g.closePath(); g.fill();
}

export function Radar() {
  const { telem, link, source, fw } = useStore();
  const wrap = useRef<HTMLDivElement>(null);
  const cv = useRef<HTMLCanvasElement>(null);
  const trail = useRef<HTMLCanvasElement | null>(null);
  const [side, setSide] = useState(0);
  const rangeRef = useRef(RANGES[0]);
  const lastTelem = useRef<Telem | null>(null);
  const lastRange = useRef(0);

  const st = useMemo(() => measure(telem), [telem]);
  // hysteresis needs the previous range; pickRange is a fixed point so a double render is harmless
  const range = pickRange(st?.p95 ?? 0, rangeRef.current);
  rangeRef.current = range;

  const live = source.kind === 'live';
  const down = live && link !== 'up';
  const sim = fw.startsWith('fake');

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSide(Math.floor(Math.min(r.width, r.height)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = cv.current;
    if (!canvas || side < 40) return;
    if (!trail.current) trail.current = document.createElement('canvas');
    const advance = telem !== lastTelem.current;
    lastTelem.current = telem;
    const reset = range !== lastRange.current;
    lastRange.current = range;
    draw(canvas, trail.current, side, telem, range, down ? 0.35 : 1, advance, reset);
  }, [telem, side, range, down]);

  const msg = down ? { text: 'LINK DOWN', cls: 'red' }
    : !telem ? { text: 'WAITING FOR SCOUT', cls: '' }
      : !st ? { text: 'NO LIDAR', cls: '' } : null;

  return (
    <div className="radar" ref={wrap}>
      <div className="radar-sq" style={{ width: side, height: side }}>
        <canvas ref={cv} style={{ width: side, height: side }} />
        <span className="rd tl">
          <span>RETURNS <b>{st?.returns ?? 0}</b>/360</span>
          {(sim || !live) && <span>{sim && <b className="rtag amber">SIMULATED</b>}{!live && <b className="rtag blue">REPLAY</b>}</span>}
        </span>
        <span className="rd tr">
          <span>RANGE <b>{range / 1000} m</b></span>
          <span className="tiny">PERSIST 1.5 s</span>
        </span>
        <span className="rd bl">NEAREST {st?.nearestMm ? <><b>{st.nearestMm} mm</b> {bearing(st.nearestDeg)}</> : 'none'}</span>
        <span className="rd br">{st ? gapsText(st.counts) : 'NO GAPS'}</span>
        {msg && <span className={`msg ${msg.cls}`}>{msg.text}</span>}
      </div>
    </div>
  );
}
