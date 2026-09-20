// OWNER: eye agent. What Scout sees: a first-person render of telem.scan, one slab per degree,
// coloured by range with the distance scale every view shares. Nothing here is inferred: no pose,
// no map, no smoothing. A bearing with no return is a dotted line, not a guess, and a gap the lidar
// could not see through is labelled UNVERIFIED with no width, because a doorway and a wall that
// does not reflect look the same in one rotation.
import { useEffect, useRef, useState } from 'react';
import { useStore, type Source } from './store';
import { widthRule, type ScanGap, type Telem } from './protocol';
import { C, distanceColour } from './palette';
import './eye.css';

const FONT = 'ui-monospace, Menlo, Consolas, monospace';
const FLOOR_MM = [500, 1000, 2000, 4000];     // reference ranges drawn on the floor
const SCALE_MM = [400, 860, 2000, 5000];      // the stops of distanceColour, labelled on the legend
const SCALE_BLOCKS = 32;                      // the legend is discrete steps, like the beams
const fmt = (n: number) => Math.round(n).toLocaleString('en-US');
// bearing in degrees counter-clockwise of straight ahead -> signed, left positive, in [-180, 180)
const signed = (deg: number) => ((deg % 360) + 540) % 360 - 180;
const side = (b: number) => (b > 0 ? `L${b}` : b < 0 ? `R${-b}` : '0');

// The front field of view by default; 360 shows the whole rotation with behind at both edges.
export function ScoutEye({ fov = 120, compact = false }: { fov?: number; compact?: boolean }) {
  const telem = useStore((s) => s.telem);
  const link = useStore((s) => s.link);
  const fw = useStore((s) => s.fw);
  const source = useStore((s) => s.source);
  const rules = useStore((s) => s.rules);
  const detail = useStore((s) => s.detail);
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  // Sized by the container, never the other way round: the canvas is absolute inside the box.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      setSize((s) => (s.w === width && s.h === height ? s : { w: width, h: height }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // One redraw per telem frame (10 Hz), and one per state or size change. No animation loop.
  useEffect(() => {
    const cv = canvas.current;
    if (!cv || !size.w || !size.h) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const W = Math.round(size.w * dpr), H = Math.round(size.h * dpr);
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    draw(ctx, W, H, dpr, { telem, link, fw, source, detail, limit: widthRule(rules)?.limit ?? 860, fov, compact });
  }, [telem, link, fw, source, rules, detail, fov, compact, size]);

  return (
    <div ref={box} className="eye">
      <canvas ref={canvas} />
    </div>
  );
}

interface Input {
  telem: Telem | null; link: 'up' | 'down'; fw: string; source: Source; detail: string;
  limit: number; fov: number; compact: boolean;
}

// Everything is drawn in device pixels so 1 px lines and slab gaps stay crisp on a phone.
function draw(ctx: CanvasRenderingContext2D, W: number, H: number, dpr: number, f: Input) {
  const px = (n: number) => Math.round(n * dpr);
  const font = (size: number) => `${Math.round(size * dpr)}px ${FONT}`;
  const { telem, fov } = f;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, W, H);

  const band = px(16);                                   // bearing ticks along the top
  const legend = f.compact ? 0 : px(22);                 // colour scale along the bottom
  const top = band, bottom = H - legend, PH = bottom - top;
  const horizon = top + PH / 2;
  const xOf = (b: number) => ((fov / 2 - b) / fov) * W;   // signed bearing -> x. Scout's left is on the left.
  // A wall of constant height: 1000 mm fills 45% of the panorama, clamped between 2 px and all of it.
  const slabH = (r: number) => Math.min(PH, Math.max(px(2), (450 * PH) / r));

  // The frame of reference is drawn even before the first frame arrives.
  ticks(ctx, W, band, dpr, fov, xOf);
  if (!f.compact) scaleStrip(ctx, W, H, bottom, dpr, f.limit);

  if (!telem) {
    message(ctx, W, horizon, dpr, 'WAITING FOR SCOUT', C.dim, f.detail);
    stateTags(ctx, W, top, dpr, f);
    return;
  }
  if (!telem.lidar) {
    message(ctx, W, horizon, dpr, 'LIDAR OFF', C.amber, 'scan is empty: the lidar is not connected');
    stateTags(ctx, W, top, dpr, f);
    return;
  }

  const down = f.source.kind === 'live' && f.link !== 'up';
  const alpha = down ? 0.35 : 1;
  ctx.globalAlpha = alpha;
  const scan = telem.scan;

  // Floor and horizon, behind the walls: a nearer wall hides the reference line for a farther range.
  ctx.fillStyle = C.rule;
  ctx.fillRect(0, Math.round(horizon), W, 1);
  const floorY = FLOOR_MM.map((mm) => Math.round(horizon + slabH(mm) / 2));
  for (const y of floorY) ctx.fillRect(0, y, W, 1);

  // One slab per degree bin. Bin i covers bearings [i, i+1), so the view holds exactly fov bins.
  const sw = W / fov;
  const gap = sw >= 4 * dpr ? px(1) : sw >= 3 ? 1 : 0;
  ctx.beginPath();   // no-return bins, stroked once as one dotted path
  let ahead = 0, nearest = 0, nearestB = 0;
  for (let k = 0; k < fov; k++) {
    const b = Math.floor(fov / 2) - 1 - k;                     // lower bearing of this bin
    const i = ((b % 360) + 360) % 360;
    const r = scan[i] ?? 0;
    const x0 = Math.round(xOf(b + 1)), x1 = Math.round(xOf(b));
    if (r > 0) {
      const h = slabH(r);
      ctx.fillStyle = distanceColour(r);
      ctx.fillRect(x0, Math.round(horizon - h / 2), Math.max(1, x1 - x0 - gap), Math.round(h));
      if (!nearest || r < nearest) { nearest = r; nearestB = b; }
      if (b >= -5 && b < 5 && (!ahead || r < ahead)) ahead = r;
    } else {
      const x = (x0 + x1) / 2;
      ctx.moveTo(x, top); ctx.lineTo(x, bottom);
    }
  }
  ctx.globalAlpha = alpha * 0.45;
  ctx.strokeStyle = C.dim;
  ctx.lineWidth = 1;
  ctx.setLineDash([px(1), px(3)]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = alpha;

  // Floor labels at the left edge, over the walls, with a dark backing so they stay legible.
  ctx.font = font(9);
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  FLOOR_MM.forEach((mm, n) => {
    const y = floorY[n];
    if (y > bottom - px(4)) return;
    const s = fmt(mm);
    const w = ctx.measureText(s).width;
    ctx.fillStyle = 'rgba(0,0,0,0.7)';
    ctx.fillRect(px(2), y - px(10), w + px(4), px(11));
    ctx.fillStyle = C.dim;
    ctx.fillText(s, px(4), y - px(2));
  });

  gaps(ctx, W, top + (f.compact ? px(16) : px(32)), dpr, fov, xOf, telem.gaps, f.compact ? 2 : 4);

  if (!f.compact) {
    ctx.font = font(12);
    ctx.textAlign = 'left';
    row(ctx, px(6), top + px(14), [['AHEAD ', C.dim], [ahead ? `${fmt(ahead)} mm` : 'no return', ahead ? C.fg : C.dim]]);
    row(ctx, px(6), top + px(28), [['NEAREST ', C.dim], [nearest ? `${fmt(nearest)} mm ${side(nearestB)}` : 'no return', nearest ? C.fg : C.dim]]);
  }

  ctx.globalAlpha = 1;
  if (down) message(ctx, W, horizon, dpr, 'LINK DOWN', C.red, f.detail || 'last frame shown');
  stateTags(ctx, W, top, dpr, f);
}

// Bearing ticks every 10 degrees along the top edge, labelled L60 L30 0 R30 R60 (or every 60 when
// the view is wide and the screen is narrow).
function ticks(ctx: CanvasRenderingContext2D, W: number, band: number, dpr: number, fov: number, xOf: (b: number) => number) {
  const px = (n: number) => Math.round(n * dpr);
  const half = Math.floor(fov / 2);
  const labelStep = (30 / fov) * W >= px(34) ? 30 : 60;
  ctx.fillStyle = C.rule;
  ctx.fillRect(0, band - 1, W, 1);
  ctx.font = `${px(9)}px ${FONT}`;
  ctx.textBaseline = 'alphabetic';
  for (let b = -half; b <= half; b += 10) {
    const x = Math.round(xOf(b));
    ctx.fillStyle = C.dim;
    ctx.fillRect(Math.min(x, W - 1), band - px(4), 1, px(4));
    if (b % labelStep) continue;
    ctx.textAlign = b === half ? 'left' : b === -half ? 'right' : 'center';
    ctx.fillStyle = b === 0 ? C.fg : C.dim;
    ctx.fillText(side(b), Math.min(Math.max(x, px(1)), W - px(1)), band - px(6));
  }
}

// Brackets over the openings found in this rotation. Only a see-through gap gets its width: the
// lidar saw something past it. UNVERIFIED is an arc of no-returns, which is a doorway or a black
// wall, so it is a candidate and never a number. Labels are packed into lanes so they never overlap;
// a gap that does not fit keeps its bracket and loses its label.
function gaps(ctx: CanvasRenderingContext2D, W: number, y0: number, dpr: number, fov: number, xOf: (b: number) => number, list: ScanGap[], maxLanes: number) {
  const px = (n: number) => Math.round(n * dpr);
  const rank = { see_through: 0, unverified: 1, step: 2 };
  const sorted = list.slice().sort((a, b) => rank[a.evidence] - rank[b.evidence] || b.width_mm - a.width_mm);
  const laneH = px(14);
  const lanes: [number, number][][] = Array.from({ length: maxLanes }, () => []);
  const pad = px(6);
  ctx.font = `${px(10)}px ${FONT}`;
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  ctx.lineWidth = 1;
  for (const g of sorted) {
    // the span is counter-clockwise from a0 to a1; a copy shifted by a turn catches a gap across the edge
    const span = ((g.a1 - g.a0) % 360 + 360) % 360;
    const b0 = signed(g.a0);
    const colour = g.evidence === 'see_through' ? C.green : g.evidence === 'unverified' ? C.amber : C.dim;
    const label = g.evidence === 'see_through' ? `${fmt(g.width_mm)} mm SEE-THROUGH` : g.evidence === 'unverified' ? 'UNVERIFIED' : `STEP ${fmt(g.width_mm)}`;
    for (const start of [b0, b0 - 360, b0 + 360]) {
      const lo = Math.max(start, -fov / 2), hi = Math.min(start + span, fov / 2);
      if (hi <= lo) continue;
      const xL = Math.round(xOf(hi)), xR = Math.round(xOf(lo));
      const tw = ctx.measureText(label).width;
      const tx = Math.min(Math.max((xL + xR) / 2 - tw / 2, px(2)), W - tw - px(2));
      const lo2 = Math.min(xL, tx) - pad, hi2 = Math.max(xR, tx + tw) + pad;
      let lane = lanes.findIndex((l) => l.every(([a, b]) => hi2 < a || lo2 > b));
      const labelled = lane >= 0;
      if (labelled) lanes[lane].push([lo2, hi2]); else lane = maxLanes;
      const yb = y0 + lane * laneH + px(12);
      ctx.strokeStyle = colour;
      ctx.beginPath();
      ctx.moveTo(xL + 0.5, yb + px(3)); ctx.lineTo(xL + 0.5, yb + 0.5); ctx.lineTo(xR + 0.5, yb + 0.5); ctx.lineTo(xR + 0.5, yb + px(3));
      ctx.stroke();
      if (labelled) { ctx.fillStyle = colour; ctx.fillText(label, tx, yb - px(3)); }
    }
  }
}

// The colour scale along the bottom, as discrete steps, with the code width named.
function scaleStrip(ctx: CanvasRenderingContext2D, W: number, H: number, bottom: number, dpr: number, limit: number) {
  const px = (n: number) => Math.round(n * dpr);
  ctx.fillStyle = C.rule;
  ctx.fillRect(0, bottom, W, 1);
  const x0 = px(6), barW = Math.min(Math.round(W * 0.45), px(240)), y = bottom + px(5), h = px(5);
  const lo = SCALE_MM[0], hi = SCALE_MM[SCALE_MM.length - 1];
  const mmAt = (t: number) => lo * Math.pow(hi / lo, t);
  const xAt = (mm: number) => x0 + (Math.log(mm / lo) / Math.log(hi / lo)) * barW;
  for (let k = 0; k < SCALE_BLOCKS; k++) {
    const a = Math.round(x0 + (k / SCALE_BLOCKS) * barW), b = Math.round(x0 + ((k + 1) / SCALE_BLOCKS) * barW);
    ctx.fillStyle = distanceColour(mmAt((k + 0.5) / SCALE_BLOCKS));
    ctx.fillRect(a, y, Math.max(1, b - a - 1), h);
  }
  ctx.font = `${px(9)}px ${FONT}`;
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = C.dim;
  SCALE_MM.forEach((mm, n) => {
    ctx.textAlign = n === 0 ? 'left' : n === SCALE_MM.length - 1 ? 'right' : 'center';
    ctx.fillText(String(mm), Math.round(xAt(mm)), H - px(3));
  });
  ctx.textAlign = 'right';
  ctx.fillText(`${fmt(limit)} mm = OBC clear width`, W - px(6), bottom + px(14));
}

// SIMULATED and REPLAY, top right, always: a judge must never mistake a simulation for the robot.
function stateTags(ctx: CanvasRenderingContext2D, W: number, top: number, dpr: number, f: Input) {
  const px = (n: number) => Math.round(n * dpr);
  const tags: [string, string][] = [];
  if (f.source.kind === 'replay') {
    const name = f.source.name.replace(/\.ndjson$/, '');
    tags.push([`REPLAY ${name.length > 28 ? name.slice(0, 27) + '…' : name}`, C.blue]);
  }
  if (f.fw.startsWith('fake')) tags.push(['SIMULATED', C.amber]);
  ctx.globalAlpha = 1;
  ctx.font = `${px(10)}px ${FONT}`;
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  ctx.lineWidth = 1;
  let x = W - px(4);
  for (const [text, colour] of tags.reverse()) {
    const w = ctx.measureText(text).width + px(8);
    x -= w;
    ctx.fillStyle = 'rgba(0,0,0,0.75)';
    ctx.fillRect(x, top + px(3), w, px(14));
    ctx.strokeStyle = colour;
    ctx.strokeRect(x + 0.5, top + px(3) + 0.5, w - 1, px(14) - 1);
    ctx.fillStyle = colour;
    ctx.fillText(text, x + px(4), top + px(13));
    x -= px(4);
  }
}

// A state message across the middle: WAITING FOR SCOUT, LINK DOWN, LIDAR OFF. Dark backing, no box.
function message(ctx: CanvasRenderingContext2D, W: number, y: number, dpr: number, text: string, colour: string, sub = '') {
  const px = (n: number) => Math.round(n * dpr);
  ctx.globalAlpha = 1;
  ctx.font = `${px(14)}px ${FONT}`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
  const w = Math.max(ctx.measureText(text).width, sub ? ctx.measureText(sub).width * 0.75 : 0) + px(24);
  ctx.fillStyle = 'rgba(0,0,0,0.8)';
  ctx.fillRect(W / 2 - w / 2, y - px(16), w, sub ? px(36) : px(24));
  ctx.fillStyle = colour;
  ctx.fillText(text, W / 2, y + px(2));
  if (sub) {
    ctx.font = `${px(10)}px ${FONT}`;
    ctx.fillStyle = C.dim;
    ctx.fillText(sub, W / 2, y + px(16));
  }
}

function row(ctx: CanvasRenderingContext2D, x: number, y: number, parts: [string, string][]) {
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  for (const [text, colour] of parts) {
    ctx.fillStyle = colour;
    ctx.fillText(text, x, y);
    x += ctx.measureText(text).width;
  }
}
