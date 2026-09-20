// The 2D map. Canvas, no libraries. Drawn like a floor plan: the fitted room as a bold outline on
// white, every surface the lidar hit in ink, an obstacle it has seen all the way round as a solid
// block, the path it drove as a dashed line, a stamp for every placed event, and Scout itself as
// the logo with an arc ahead of it for how far the way is clear.
// Room frame throughout (PROTOCOL.md section 4), y drawn upward.
import { useEffect, useRef } from 'react';
import { useStore, placedEvents } from './store';
import type { MapFrame, ScoutEvent, Telem } from './protocol';
import logo from './assets/scout-64.png';

// Scout on the map is the logo. Loaded once; until it arrives the fallback is a plain dot.
const ROBOT = new Image();
ROBOT.src = logo;

// Same palette as style.css.
const C = {
  paper: '#e4e4e1', grid: '#c6c6c2', free: '#ffffff', ink: '#2b2925', body: '#8a8781',
  range: 'rgba(43, 41, 37, .5)', path: 'rgba(43, 41, 37, .7)',
  robot: '#e9b74f', fail: '#c93535', pass: '#237a4a', ramp: '#b5841f', obstacle: '#4d4a44',
};
const FONT = '"IBM Plex Mono", ui-monospace, monospace';
const PAD = 28;
const GRID_MM = 500;
const SUB = 4;               // sub-cells per grid cell in the cached image, so a surface can be drawn fatter than a cell
const RANGE_MM = 1000;       // the arc ahead of Scout reaches this far when nothing is closer
const RANGE_DEG = 35;        // half-width of that arc

const colorFor = (k: ScoutEvent['kind']) =>
  k === 'width_fail' ? C.fail : k === 'width_pass' ? C.pass : k === 'ramp' ? C.ramp : C.obstacle;

// What each marker says on the map. Short: the feed carries the full line.
// The stamp beside a pin. An obstacle gets none: Scout has no camera to name it, and a map of
// "unknown" stamps says nothing the grey block under the pin does not.
const tagFor = (e: ScoutEvent) =>
  e.kind === 'width_fail' || e.kind === 'width_pass' ? `${e.value} mm`
    : e.kind === 'mark' ? (e.label || 'mark')
      : e.label && e.label !== 'unknown' ? e.label : '';

type Room = { w_mm: number; l_mm: number };

// World extent to draw: the fitted room if there is one, else the grid, else a default 6 m box.
function bounds(map: MapFrame | null, room: Room | null) {
  if (room) return { x0: 0, y0: 0, x1: room.w_mm, y1: room.l_mm };
  if (map) {
    const [ox, oy] = map.origin;
    return { x0: ox - map.cell_mm / 2, y0: oy - map.cell_mm / 2, x1: ox + map.w * map.cell_mm, y1: oy + map.h * map.cell_mm };
  }
  return { x0: 0, y0: 0, x1: 6000, y1: 6000 };
}

// Unknown cells inside the room that no seen floor can reach without crossing a surface or a
// fitted wall: the inside of an obstacle Scout has seen all the way round, or a slot behind one
// too tight to look into. Shadows behind things are unknown too, but they touch seen floor.
function enclosed(map: MapFrame, room: Room, band: number) {
  const n = map.w * map.h, reach = new Uint8Array(n), queue = new Int32Array(n);
  const [ox, oy] = map.origin;
  const inRoom = (i: number) => {
    const x = ox + (i % map.w) * map.cell_mm, y = oy + ((i / map.w) | 0) * map.cell_mm;
    return x > band && y > band && x < room.w_mm - band && y < room.l_mm - band;
  };
  let head = 0, tail = 0;
  for (let i = 0; i < n; i++) if (map.cells[i] === '1') { reach[i] = 1; queue[tail++] = i; }
  while (head < tail) {
    const i = queue[head++], cx = i % map.w, cy = (i / map.w) | 0;
    const next = [cx > 0 ? i - 1 : -1, cx < map.w - 1 ? i + 1 : -1, cy > 0 ? i - map.w : -1, cy < map.h - 1 ? i + map.w : -1];
    for (const j of next) if (j >= 0 && !reach[j] && map.cells[j] !== '2' && inRoom(j)) { reach[j] = 1; queue[tail++] = j; }
  }
  const out: number[] = [];
  for (let i = 0; i < n; i++) if (map.cells[i] === '0' && !reach[i] && inRoom(i)) out.push(i);
  return out;
}

// The occupancy grid as an image, one map frame at a time. With a fitted room the floor is the
// white room itself, so only surfaces and enclosed obstacle bodies are drawn; without one, seen
// floor is drawn white on the paper. A surface inside the room is drawn one and a half cells wide so
// an obstacle reads as a solid edge and not a row of specks. Hits on the fitted walls themselves
// are left to the outline, which is drawn from the fit.
let cache: { map: MapFrame; room: Room | null; img: HTMLCanvasElement } | null = null;
function gridImage(map: MapFrame, room: Room | null) {
  if (cache && cache.map === map && cache.room?.w_mm === room?.w_mm && cache.room?.l_mm === room?.l_mm) return cache.img;   // room is a fresh object every frame
  const img = document.createElement('canvas');
  img.width = map.w * SUB; img.height = map.h * SUB;
  const g = img.getContext('2d')!;
  const [ox, oy] = map.origin;
  const band = map.cell_mm * 3;   // this close to a fitted wall, an occupied cell is the wall
  const row = (cy: number) => (map.h - 1 - cy) * SUB;   // row 0 is y = 0, which is the bottom
  const hits: number[] = [];
  g.fillStyle = C.free;
  for (let i = 0; i < map.cells.length; i++) {
    const c = map.cells[i];
    if (c === '0') continue;
    const cx = i % map.w, cy = (i / map.w) | 0;
    if (c === '1') { if (!room) g.fillRect(cx * SUB, row(cy), SUB, SUB); continue; }
    const x = ox + cx * map.cell_mm, y = oy + cy * map.cell_mm;
    if (!room || (x > band && y > band && x < room.w_mm - band && y < room.l_mm - band)) hits.push(i);
  }
  if (room) {
    g.fillStyle = C.body;
    for (const i of enclosed(map, room, band)) g.fillRect((i % map.w) * SUB, row((i / map.w) | 0), SUB, SUB);
  }
  g.fillStyle = C.ink;
  const fat = room ? Math.round(SUB * 1.5) : SUB, off = Math.round((fat - SUB) / 2);
  for (const i of hits) g.fillRect((i % map.w) * SUB - off, row((i / map.w) | 0) - off, fat, fat);
  cache = { map, room, img };
  return img;
}

function draw(cv: HTMLCanvasElement, map: MapFrame | null, telem: Telem | null, events: ScoutEvent[], trail: [number, number][]) {
  const dpr = window.devicePixelRatio || 1;
  const cw = cv.clientWidth, ch = cv.clientHeight;
  if (!cw || !ch) return;
  if (cv.width !== cw * dpr || cv.height !== ch * dpr) { cv.width = cw * dpr; cv.height = ch * dpr; }
  const g = cv.getContext('2d');
  if (!g) return;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = C.paper;
  g.fillRect(0, 0, cw, ch);

  const room = telem?.room ?? null;
  const b = bounds(map, room);
  const s = Math.min((cw - 2 * PAD) / (b.x1 - b.x0), (ch - 2 * PAD) / (b.y1 - b.y0));
  if (!(s > 0)) return;
  const offX = (cw - (b.x1 - b.x0) * s) / 2, offY = (ch - (b.y1 - b.y0) * s) / 2;
  const px = (x: number) => offX + (x - b.x0) * s;
  const py = (y: number) => ch - offY - (y - b.y0) * s;   // y up

  // graph paper: a line every half metre in the room frame, across the whole canvas
  g.strokeStyle = C.grid; g.lineWidth = 1;
  g.beginPath();
  for (let x = Math.floor((b.x0 - offX / s) / GRID_MM) * GRID_MM; px(x) <= cw; x += GRID_MM) {
    const X = Math.round(px(x)) + 0.5; g.moveTo(X, 0); g.lineTo(X, ch);
  }
  for (let y = Math.floor((b.y0 - offY / s) / GRID_MM) * GRID_MM; py(y) >= 0; y += GRID_MM) {
    const Y = Math.round(py(y)) + 0.5; g.moveTo(0, Y); g.lineTo(cw, Y);
  }
  g.stroke();

  // the room floor, then what the lidar has found on it
  if (room) {
    g.fillStyle = C.free;
    g.fillRect(px(0), py(room.l_mm), px(room.w_mm) - px(0), py(0) - py(room.l_mm));
  }
  if (map) {
    const [ox, oy] = map.origin;
    g.save();
    g.imageSmoothingEnabled = false;
    g.drawImage(gridImage(map, room), px(ox - map.cell_mm / 2), py(oy + (map.h - 0.5) * map.cell_mm), map.w * map.cell_mm * s, map.h * map.cell_mm * s);
    g.restore();
  }

  // the fitted walls, always continuous: they are what the room frame is built on
  if (room) {
    g.strokeStyle = C.ink; g.lineWidth = 3;
    g.strokeRect(px(0), py(room.l_mm), px(room.w_mm) - px(0), py(0) - py(room.l_mm));
  }

  // the path driven so far
  if (trail.length > 1) {
    g.strokeStyle = C.path; g.lineWidth = 2; g.lineJoin = 'round'; g.lineCap = 'round';
    g.setLineDash([6, 5]);
    g.beginPath(); g.moveTo(px(trail[0][0]), py(trail[0][1]));
    for (let i = 1; i < trail.length; i++) g.lineTo(px(trail[i][0]), py(trail[i][1]));
    g.stroke();
    g.setLineDash([]);
  }

  // markers: a pin and a stamp with the reading or the name
  g.font = `600 12px ${FONT}`;
  g.textBaseline = 'middle';
  for (const e of events) {
    const x = px(e.x_mm!), y = py(e.y_mm!), col = colorFor(e.kind);
    g.fillStyle = col;
    g.beginPath(); g.arc(x, y, 6, 0, Math.PI * 2); g.fill();
    g.strokeStyle = '#fff'; g.lineWidth = 2; g.stroke();
    const tag = tagFor(e);
    if (!tag) continue;
    const w = g.measureText(tag).width + 12;
    g.fillStyle = 'rgba(255,255,255,.94)';
    g.fillRect(x + 10, y - 10, w, 20);
    g.strokeStyle = col; g.lineWidth = 1;
    g.strokeRect(x + 10.5, y - 9.5, w - 1, 19);
    g.fillStyle = col;
    g.fillText(tag, x + 16, y + 1);
  }

  // Scout: the logo in a round badge, with a tick for heading and an arc ahead for how far the way
  // is clear (the nearest return within RANGE_DEG, capped at RANGE_MM).
  if (telem?.pose) {
    const a = (telem.heading_deg * Math.PI) / 180;
    const x = px(telem.x_mm), y = py(telem.y_mm), r = 17;
    let reach = RANGE_MM;
    if (telem.scan.length === 360) {
      for (let d = -RANGE_DEG; d <= RANGE_DEG; d++) { const mm = telem.scan[(d + 360) % 360]; if (mm && mm < reach) reach = mm; }
    }
    const R = reach * s;
    if (R > r + 6) {
      g.strokeStyle = C.range; g.lineWidth = 2.5; g.lineCap = 'round';
      const half = (RANGE_DEG * Math.PI) / 180;
      g.beginPath(); g.arc(x, y, R, -a - half, -a + half); g.stroke();   // canvas angles run clockwise, the room's run counter-clockwise
    }
    g.strokeStyle = C.ink; g.lineWidth = 2; g.lineCap = 'butt';
    g.beginPath(); g.moveTo(x, y); g.lineTo(x + Math.cos(a) * (r + 12), y - Math.sin(a) * (r + 12)); g.stroke();
    g.save();
    g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.closePath();
    g.fillStyle = '#fff'; g.fill(); g.clip();
    if (ROBOT.complete && ROBOT.naturalWidth) g.drawImage(ROBOT, x - r, y - r, 2 * r, 2 * r);
    else { g.fillStyle = C.robot; g.beginPath(); g.arc(x, y, r * 0.6, 0, Math.PI * 2); g.fill(); }
    g.restore();
    g.strokeStyle = C.ink; g.lineWidth = 1.5;
    g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.stroke();
  }

  // scale bar: one metre
  const m = 1000 * s;
  if (m > 24 && m < cw - 2 * PAD) {
    g.strokeStyle = C.ink; g.lineWidth = 2;
    g.beginPath(); g.moveTo(PAD, ch - 14); g.lineTo(PAD + m, ch - 14); g.stroke();
    g.fillStyle = C.ink; g.font = `500 12px ${FONT}`; g.fillText('1 m', PAD + m + 8, ch - 14);
  }
}

// The canvas on screen, for the PNG download. There is one map on the page.
let canvasEl: HTMLCanvasElement | null = null;
export const mapImage = () => new Promise<Blob | null>((resolve) => (canvasEl ? canvasEl.toBlob(resolve, 'image/png') : resolve(null)));

export function MapView() {
  const ref = useRef<HTMLCanvasElement>(null);
  const { map, telem, events, trail } = useStore();
  const placed = placedEvents(events);

  useEffect(() => {
    const cv = ref.current;
    if (cv) draw(cv, map, telem, placed, trail);
  });

  useEffect(() => {
    const redraw = () => {
      const cv = ref.current, st = useStore.getState();
      if (cv) draw(cv, st.map, st.telem, placedEvents(st.events), st.trail);
    };
    canvasEl = ref.current;
    window.addEventListener('resize', redraw);
    ROBOT.addEventListener('load', redraw);
    return () => { canvasEl = null; window.removeEventListener('resize', redraw); ROBOT.removeEventListener('load', redraw); };
  }, []);

  const room = telem?.room;
  return (
    <section className="sheet mapwrap">
      <canvas ref={ref} className="map" />
      <div className="maphead">
        <h2>Map</h2>
        {room && <span className="small">{(room.w_mm / 1000).toFixed(2)} × {(room.l_mm / 1000).toFixed(2)} m</span>}
        {telem && !telem.pose && <span className="tag warn">No pose</span>}
        {telem && !telem.lidar && <span className="tag warn">No lidar</span>}
      </div>
      <div className="legend">
        <span><i className="sq" style={{ background: C.ink }} />surface</span>
        <span><i className="sq" style={{ background: C.body }} />obstacle body</span>
        <span><i className="arc" />range</span>
        <span><i className="path" />path</span>
        <span className="sep" />
        <span><i style={{ background: C.fail }} />too narrow</span>
        <span><i style={{ background: C.pass }} />clear</span>
        <span><i style={{ background: C.obstacle }} />obstacle</span>
      </div>
    </section>
  );
}
