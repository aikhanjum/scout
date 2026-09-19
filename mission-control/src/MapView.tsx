// The 2D map. Canvas, no libraries: the occupancy grid, the live scan, Scout, and a marker
// for every placed event. Room frame throughout (PROTOCOL.md section 4), y drawn upward.
import { useEffect, useRef } from 'react';
import { useStore, placedEvents } from './store';
import type { MapFrame, ScoutEvent, Telem } from './protocol';

const C = {
  bg: '#0b0e14', unknown: '#121821', free: '#1b2431', occupied: '#54687f',
  scan: '#4da3ff', robot: '#e6edf3', fail: '#ff4d4f', pass: '#3ddc84', ramp: '#ffb020', obstacle: '#8b98a5',
};
const PAD = 28;

const colorFor = (k: ScoutEvent['kind']) =>
  k === 'width_fail' ? C.fail : k === 'width_pass' ? C.pass : k === 'ramp' ? C.ramp : C.obstacle;

// What each marker says on the map. Short: the feed carries the full line.
const tagFor = (e: ScoutEvent) =>
  e.kind === 'width_fail' || e.kind === 'width_pass' ? `${e.value} mm`
    : e.kind === 'mark' ? (e.label || 'mark')
      : (e.label || 'unknown');

// World extent to draw: the grid if there is one, else the room, else a default 6 m box.
function bounds(map: MapFrame | null, telem: Telem | null) {
  if (map) {
    const [ox, oy] = map.origin;
    return { x0: ox - map.cell_mm / 2, y0: oy - map.cell_mm / 2, x1: ox + map.w * map.cell_mm, y1: oy + map.h * map.cell_mm };
  }
  if (telem?.room) return { x0: 0, y0: 0, x1: telem.room.w_mm, y1: telem.room.l_mm };
  return { x0: 0, y0: 0, x1: 6000, y1: 6000 };
}

function draw(cv: HTMLCanvasElement, map: MapFrame | null, telem: Telem | null, events: ScoutEvent[]) {
  const dpr = window.devicePixelRatio || 1;
  const cw = cv.clientWidth, ch = cv.clientHeight;
  if (!cw || !ch) return;
  if (cv.width !== cw * dpr || cv.height !== ch * dpr) { cv.width = cw * dpr; cv.height = ch * dpr; }
  const g = cv.getContext('2d');
  if (!g) return;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = C.bg;
  g.fillRect(0, 0, cw, ch);

  const b = bounds(map, telem);
  const s = Math.min((cw - 2 * PAD) / (b.x1 - b.x0), (ch - 2 * PAD) / (b.y1 - b.y0));
  const offX = (cw - (b.x1 - b.x0) * s) / 2, offY = (ch - (b.y1 - b.y0) * s) / 2;
  const px = (x: number) => offX + (x - b.x0) * s;
  const py = (y: number) => ch - offY - (y - b.y0) * s;   // y up

  // occupancy grid
  if (map) {
    const [ox, oy] = map.origin;
    const cell = map.cell_mm * s;
    for (let i = 0; i < map.cells.length; i++) {
      const c = map.cells[i];
      if (c === '0') continue;
      g.fillStyle = c === '2' ? C.occupied : C.free;
      const cx = i % map.w, cy = (i / map.w) | 0;
      g.fillRect(px(ox + (cx - 0.5) * map.cell_mm), py(oy + (cy + 0.5) * map.cell_mm), Math.ceil(cell), Math.ceil(cell));
    }
  }

  // live scan, placed in the room frame through the current pose
  if (telem?.pose && telem.scan.length === 360) {
    g.fillStyle = C.scan;
    for (let i = 0; i < 360; i++) {
      const mm = telem.scan[i];
      if (!mm) continue;
      const a = ((telem.heading_deg + i) * Math.PI) / 180;
      g.fillRect(px(telem.x_mm + Math.cos(a) * mm) - 1, py(telem.y_mm + Math.sin(a) * mm) - 1, 2, 2);
    }
  }

  // markers
  g.font = '600 12px system-ui, sans-serif';
  g.textBaseline = 'middle';
  for (const e of events) {
    const x = px(e.x_mm!), y = py(e.y_mm!);
    g.fillStyle = colorFor(e.kind);
    g.beginPath(); g.arc(x, y, 6, 0, Math.PI * 2); g.fill();
    g.strokeStyle = C.bg; g.lineWidth = 2; g.stroke();
    const tag = tagFor(e);
    const w = g.measureText(tag).width;
    g.fillStyle = 'rgba(11,14,20,.82)';
    g.fillRect(x + 10, y - 9, w + 10, 18);
    g.fillStyle = colorFor(e.kind);
    g.fillText(tag, x + 15, y + 1);
  }

  // Scout: a triangle pointing along heading
  if (telem?.pose) {
    const a = (telem.heading_deg * Math.PI) / 180;
    const x = px(telem.x_mm), y = py(telem.y_mm), r = 11;
    g.fillStyle = telem.measuring ? C.ramp : C.robot;
    g.beginPath();
    for (const d of [0, 2.5, -2.5]) g.lineTo(x + Math.cos(a + d) * r, y - Math.sin(a + d) * r);
    g.closePath(); g.fill();
  }

  // scale bar: one metre
  const m = 1000 * s;
  if (m > 24 && m < cw - 2 * PAD) {
    g.strokeStyle = C.occupied; g.lineWidth = 2;
    g.beginPath(); g.moveTo(PAD, ch - 14); g.lineTo(PAD + m, ch - 14); g.stroke();
    g.fillStyle = C.occupied; g.fillText('1 m', PAD + m + 8, ch - 14);
  }
}

export function MapView() {
  const ref = useRef<HTMLCanvasElement>(null);
  const { map, telem, events } = useStore();
  const placed = placedEvents(events);

  useEffect(() => {
    const cv = ref.current;
    if (cv) draw(cv, map, telem, placed);
  });

  useEffect(() => {
    const redraw = () => { const cv = ref.current; if (cv) draw(cv, useStore.getState().map, useStore.getState().telem, placedEvents(useStore.getState().events)); };
    window.addEventListener('resize', redraw);
    return () => window.removeEventListener('resize', redraw);
  }, []);

  const room = telem?.room;
  return (
    <div className="mapwrap">
      <canvas ref={ref} className="map" />
      <div className="maphead">
        <span className="maptitle">MAP</span>
        {room && <span className="muted">{(room.w_mm / 1000).toFixed(2)} × {(room.l_mm / 1000).toFixed(2)} m</span>}
        {telem && !telem.pose && <span className="tag">NO POSE</span>}
        {telem && !telem.lidar && <span className="tag">NO LIDAR</span>}
      </div>
      <div className="legend">
        <i style={{ background: C.fail }} />too narrow
        <i style={{ background: C.pass }} />clear
        <i style={{ background: C.ramp }} />ramp
        <i style={{ background: C.obstacle }} />obstacle
      </div>
    </div>
  );
}
