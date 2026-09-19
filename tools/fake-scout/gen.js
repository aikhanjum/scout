// Writes one scripted room survey in protocol v2 NDJSON (docs/PROTOCOL.md section 7).
// A tiny 2D simulator: a rectangular room with obstacles, a robot wall-following the perimeter,
// a real 360-ray cast per frame and an occupancy grid built from those rays. Deterministic.
// Run with `npm run gen`.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] ?? path.join(here, '../../data/runs/room-scan.ndjson');

const HZ = 10;
const T0 = 1000;
const SPACE = 'E5 room 2024';
const CONFIG = { width_limit_mm: 860, robot_width_mm: 260, wall_target_mm: 300, cruise: 0.4 };
const CELL = 50;
const ROOM = { w: 4210, l: 5090 };          // x across, y along
const MAX_RANGE = 12000;

// Obstacles: axis-aligned boxes in the room frame, with what the camera would call them.
// The bin sits close to the right wall and makes the one wall-obstacle pinch that fails.
// The chair and the ramp stand in the wall-following lane, so Scout meets them, stops, names them
// and goes around. The bin leaves a 510 mm slot against the wall: too narrow, and it says so. The
// table is off the lane, so it lands on the map as geometry that Scout never got close enough to
// name -- which is the honest outcome and worth showing.
const OBSTACLES = [
  { x0: 1700, y0: 150,  x1: 2260, y1: 900,  label: 'chair', conf: 0.71, kind: 'obstacle' },
  { x0: 3510, y0: 1400, x1: 4210, y1: 2400, label: 'ramp',  conf: 0.83, kind: 'ramp' },
  { x0: 3320, y0: 3200, x1: 3700, y1: 3700, label: 'bin',   conf: 0.64, kind: 'obstacle' },
  { x0: 600,  y0: 3400, x1: 1400, y1: 3800, label: 'table', conf: 0.58, kind: 'obstacle' },
];

// Every wall of the room plus every side of every box, as segments to cast against.
const segs = [];
const box = (x0, y0, x1, y1) => {
  segs.push([x0, y0, x1, y0], [x1, y0, x1, y1], [x1, y1, x0, y1], [x0, y1, x0, y0]);
};
box(0, 0, ROOM.w, ROOM.l);
for (const o of OBSTACLES) box(o.x0, o.y0, o.x1, o.y1);

// --- ray casting -------------------------------------------------------------
// Ray from (px,py) along (dx,dy) against one segment. Returns distance or Infinity.
function hit(px, py, dx, dy, [ax, ay, bx, by]) {
  const sx = bx - ax, sy = by - ay;
  const den = dx * sy - dy * sx;
  if (Math.abs(den) < 1e-9) return Infinity;
  const t = ((ax - px) * sy - (ay - py) * sx) / den;   // along the ray
  const u = ((ax - px) * dy - (ay - py) * dx) / den;   // along the segment
  return t >= 0 && u >= 0 && u <= 1 ? t : Infinity;
}

// A full 360-entry scan from a pose, in the robot frame: index i = bearing i degrees CCW of ahead.
function scanFrom(x, y, headingDeg, jitter) {
  const scan = new Array(360);
  for (let i = 0; i < 360; i++) {
    const a = ((headingDeg + i) * Math.PI) / 180;
    const dx = Math.cos(a), dy = Math.sin(a);
    let best = Infinity;
    for (const s of segs) { const d = hit(x, y, dx, dy, s); if (d < best) best = d; }
    scan[i] = best > MAX_RANGE ? 0 : Math.max(0, Math.round(best + jitter() * 12));
  }
  return scan;
}

// --- occupancy grid ----------------------------------------------------------
const GW = Math.ceil(ROOM.w / CELL) + 2, GH = Math.ceil(ROOM.l / CELL) + 2;
const grid = new Uint8Array(GW * GH);            // 0 unknown, 1 free, 2 occupied
const at = (cx, cy) => cy * GW + cx;

// Walk the ray in cell steps marking free, then mark the endpoint occupied.
function carve(x, y, headingDeg, scan) {
  for (let i = 0; i < 360; i += 2) {
    const mm = scan[i];
    if (!mm) continue;
    const a = ((headingDeg + i) * Math.PI) / 180;
    const dx = Math.cos(a), dy = Math.sin(a);
    for (let d = 0; d < mm - CELL; d += CELL) {
      const cx = Math.round((x + dx * d) / CELL), cy = Math.round((y + dy * d) / CELL);
      if (cx >= 0 && cx < GW && cy >= 0 && cy < GH && grid[at(cx, cy)] === 0) grid[at(cx, cy)] = 1;
    }
    const ex = Math.round((x + dx * mm) / CELL), ey = Math.round((y + dy * mm) / CELL);
    if (ex >= 0 && ex < GW && ey >= 0 && ey < GH) grid[at(ex, ey)] = 2;
  }
}

const mapFrame = (t) => ({
  type: 'map', t, cell_mm: CELL, w: GW, h: GH, origin: [0, 0],
  cells: Array.from(grid).join(''), pose: true,
});

// --- the route ---------------------------------------------------------------
// Wall-follow the perimeter clockwise at wall_target_mm, pausing where Scout would stop to look.
// `stop` is the dwell in seconds: Scout holds still, measuring, while the camera classifies.
const M = CONFIG.wall_target_mm;
const route = [
  { x: M, y: M },
  { x: 1450, y: M, stop: 1.8, event: 1 },                        // pull up facing the chair
  { x: 1450, y: 1150 },                                          // round it
  { x: 2900, y: 1150 },
  { x: 2900, y: M },                                             // back down to the wall
  { x: ROOM.w - M, y: M },
  { x: ROOM.w - M, y: 1150, stop: 1.8, event: 2 },               // turn north, the ramp is ahead
  { x: 3150, y: 1150 },                                          // round the ramp
  { x: 3150, y: 2600 },
  { x: ROOM.w - M, y: 2600 },
  { x: ROOM.w - M, y: 3000, stop: 1.8, event: 3, width: true },  // the bin pinches against the wall
  { x: ROOM.w - M, y: ROOM.l - M },
  { x: M, y: ROOM.l - M },
  { x: M, y: M },
];

// --- run ---------------------------------------------------------------------
let seed = 7;
const jitter = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32) - 0.5;
const SPEED = 420;                       // mm/s at cruise 0.4
const lines = [{ type: 'run', space: SPACE, fw: 'fake-0.2.0', started_t: T0, started_at: '2026-09-19T12:00:00.000Z', config: CONFIG }];
let t = T0, seq = 0, lastMap = -Infinity;
const pending = [];                      // events to flush at the end of the current dwell

function emit(ev) { lines.push({ type: 'event', t, seq: seq++, ...ev, space: SPACE }); }

function frame(x, y, heading, v, w, measuring) {
  const scan = scanFrom(x, y, heading, jitter);
  carve(x, y, heading, scan);
  // narrowest gap across the path: the pair of returns bounding the way ahead
  const left = scan[85] || 0, right = scan[275] || 0;
  lines.push({
    // gaps: [] on purpose. The gap finder is pi/scout/gaps.py and is not ported to JS; the
    // simulator supplies the rotation and the real robot supplies the openings found in it.
    type: 'telem', t, mode: 'wall_follow', measuring, lidar: true, scan, gaps: [],
    pose: true, x_mm: Math.round(x), y_mm: Math.round(y), heading_deg: Math.round(heading * 10) / 10,
    room: { w_mm: ROOM.w, l_mm: ROOM.l },
    clearance_mm: left && right ? left + right : 0,
    bump: [0, 0], stuck: false, v, w,
  });
  // one map per 10 s, the cadence PROTOCOL.md section 7 gives a recorder: enough to watch the
  // room fill in on replay, without the grid dominating the file
  if (t - lastMap >= 10000) { lines.push(mapFrame(t)); lastMap = t; }
  t += 1000 / HZ;
}

const TURN_RATE = 70;                    // deg/s: a skid-steer wall follower arcs a corner, it does
                                         // not snap. Scout's pose tracker needs the continuity.
const wrap = (d) => ((d + 180) % 360 + 360) % 360 - 180;

emit({ kind: 'run_start' });
let px = route[0].x, py = route[0].y, heading = 0;
for (let i = 1; i < route.length; i++) {
  const wp = route[i];
  const dist = Math.hypot(wp.x - px, wp.y - py);
  const want = (Math.atan2(wp.y - py, wp.x - px) * 180) / Math.PI;

  // turn in place to face the next leg
  const delta = wrap(want - heading);
  const turnSteps = Math.round((Math.abs(delta) / TURN_RATE) * HZ);
  for (let s = 1; s <= turnSteps; s++) {
    frame(px, py, heading + (delta * s) / turnSteps, 0, Math.sign(delta) * 0.35, false);
  }
  heading = want;

  const steps = Math.max(1, Math.round((dist / SPEED) * HZ));
  for (let s = 1; s <= steps; s++) {
    frame(px + ((wp.x - px) * s) / steps, py + ((wp.y - py) * s) / steps, heading, CONFIG.cruise, 0, false);
  }
  px = wp.x; py = wp.y;

  if (wp.stop) {
    const o = OBSTACLES[wp.event - 1];
    const cx = (o.x0 + o.x1) / 2, cy = (o.y0 + o.y1) / 2;
    for (let s = 0; s < wp.stop * HZ; s++) frame(px, py, heading, 0, 0, true);
    emit({ kind: o.kind, label: o.label, confidence: o.conf, photo: `p${wp.event}`, x_mm: Math.round(cx), y_mm: Math.round(cy) });
    if (wp.width) {
      const gap = Math.round(ROOM.w - o.x1);        // bin to the right wall
      emit({ kind: gap < CONFIG.width_limit_mm ? 'width_fail' : 'width_pass', value: gap, unit: 'mm',
             limit: CONFIG.width_limit_mm, between: 'wall-obstacle', x_mm: Math.round((o.x1 + ROOM.w) / 2), y_mm: Math.round(cy) });
    }
  }
}
// the slot the chair leaves against the bottom wall: tight, but a chair can pass
emit({ kind: 'width_pass', value: 1150, unit: 'mm', limit: CONFIG.width_limit_mm, between: 'wall-obstacle', x_mm: 1980, y_mm: 1150 });
lines.push(mapFrame(t));
emit({ kind: 'run_stop' });

fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, lines.map((l) => JSON.stringify(l)).join('\n') + '\n');
const kb = (fs.statSync(out).size / 1024).toFixed(0);
console.log(`wrote ${path.relative(process.cwd(), out)}: ${lines.length - 1} frames, ${seq} events, ${((t - T0) / 1000).toFixed(1)} s, ${kb} kB`);
console.log(`room ${ROOM.w}x${ROOM.l} mm, grid ${GW}x${GH} at ${CELL} mm`);
