// The room the fake Scout lives in, and a small 2D robot inside it. Shared by gen.js (which
// writes one scripted survey to a file) and server.js (which runs the robot live so the
// dashboard can drive it). Deterministic: the only randomness is the seeded jitter below.
//
// Frames follow docs/PROTOCOL.md: room frame in mm with the origin at one corner, x along the
// longer wall, headings in degrees counter-clockwise, scans as 360 ranges CCW of straight ahead.

export const CELL = 50;
export const ROOM = { w: 4210, l: 5090 };          // x across, y along
export const MAX_RANGE = 12000;
export const ROBOT_R = 130;                        // half of robot_width_mm
export const CONFIG = { width_limit_mm: 860, robot_width_mm: 260, wall_target_mm: 300, cruise: 0.4 };

// Obstacles: axis-aligned boxes in the room frame. Scout has no camera, so it never learns what
// any of them is; the names are for whoever reads this file. The chair and the ramp stand in the
// wall-following lane, so Scout meets them head on and goes around. The bin leaves a 510 mm slot
// against the right wall: too narrow, and it says so. The table is off the lane, and Scout passes
// within arm's length of it without ever facing it, which the lidar does not mind.
export const OBSTACLES = [
  { x0: 1700, y0: 150,  x1: 2260, y1: 900 },    // a chair, in the lane along the bottom wall
  { x0: 3510, y0: 1400, x1: 4210, y1: 2400 },   // a ramp against the right wall, in the lane
  { x0: 3320, y0: 3200, x1: 3700, y1: 3700 },   // a bin, 510 mm off the right wall
  { x0: 600,  y0: 3400, x1: 1400, y1: 3800 },   // a table, 600 mm off the left wall
];

// Every wall of the room plus every side of every box, as segments to cast against.
// A segment knows which obstacle it belongs to (null for a room wall).
export const segs = [];
const box = (x0, y0, x1, y1, o = null) => {
  segs.push([x0, y0, x1, y0, o], [x1, y0, x1, y1, o], [x1, y1, x0, y1, o], [x0, y1, x0, y0, o]);
};
box(0, 0, ROOM.w, ROOM.l);
for (const o of OBSTACLES) box(o.x0, o.y0, o.x1, o.y1, o);

export function makeJitter(seed = 7) {
  return () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32) - 0.5;
}

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

// Nearest surface along a bearing: distance and the segment it belongs to.
export function cast(x, y, angleRad) {
  const dx = Math.cos(angleRad), dy = Math.sin(angleRad);
  let best = Infinity, seg = null;
  for (const s of segs) { const d = hit(x, y, dx, dy, s); if (d < best) { best = d; seg = s; } }
  return { d: best, seg };
}

// A full 360-entry scan from a pose, in the robot frame: index i = bearing i degrees CCW of ahead.
export function scanFrom(x, y, headingDeg, jitter) {
  const scan = new Array(360);
  for (let i = 0; i < 360; i++) {
    const best = cast(x, y, ((headingDeg + i) * Math.PI) / 180).d;
    scan[i] = best > MAX_RANGE ? 0 : Math.max(0, Math.round(best + jitter() * 12));
  }
  return scan;
}

// Distance from a point to the nearest surface, for keeping the robot's body out of the walls.
export function clearanceAt(x, y) {
  let best = Infinity;
  for (const [ax, ay, bx, by] of segs) {
    const sx = bx - ax, sy = by - ay, len2 = sx * sx + sy * sy;
    const u = Math.max(0, Math.min(1, ((x - ax) * sx + (y - ay) * sy) / len2));
    best = Math.min(best, Math.hypot(x - (ax + u * sx), y - (ay + u * sy)));
  }
  return best;
}

// --- occupancy grid ----------------------------------------------------------
export class Grid {
  constructor() {
    this.w = Math.ceil(ROOM.w / CELL) + 2; this.h = Math.ceil(ROOM.l / CELL) + 2;
    this.cells = new Uint8Array(this.w * this.h);   // 0 unknown, 1 free, 2 occupied
  }
  clear() { this.cells.fill(0); }
  at(cx, cy) { return cy * this.w + cx; }
  // Walk the ray in cell steps marking free, then mark the endpoint occupied.
  carve(x, y, headingDeg, scan) {
    const { w: GW, h: GH, cells } = this;
    for (let i = 0; i < 360; i += 2) {
      const mm = scan[i];
      if (!mm) continue;
      const a = ((headingDeg + i) * Math.PI) / 180;
      const dx = Math.cos(a), dy = Math.sin(a);
      for (let d = 0; d < mm - CELL; d += CELL) {
        const cx = Math.round((x + dx * d) / CELL), cy = Math.round((y + dy * d) / CELL);
        if (cx >= 0 && cx < GW && cy >= 0 && cy < GH && cells[this.at(cx, cy)] === 0) cells[this.at(cx, cy)] = 1;
      }
      const ex = Math.round((x + dx * mm) / CELL), ey = Math.round((y + dy * mm) / CELL);
      if (ex >= 0 && ex < GW && ey >= 0 && ey < GH) cells[this.at(ex, ey)] = 2;
    }
  }
  frame(t) {
    return { type: 'map', t, cell_mm: CELL, w: this.w, h: this.h, origin: [0, 0], cells: Array.from(this.cells).join(''), pose: true };
  }
}

// --- the live robot ----------------------------------------------------------
// Drives by the commands in PROTOCOL.md section 5 and behaves as section 6 describes: it reports
// each obstacle once as it comes near it, measures every pinch it passes, and in wall_follow it
// keeps the wall on its right until it has closed a loop. Call step(t, dt) at 10 Hz; it returns
// the frames to send.
const V_MAX = 1050;                 // mm/s at v = 1 (cruise 0.4 is 420 mm/s, as in the scripted run)
const W_MAX = 180;                  // deg/s at w = 1
const WATCHDOG_MS = 600;            // teleop: no drive for this long and the motors stop
const PINCH_MS = 3000;              // a pinch closes after this long at the latest
const PINCH_MIN_MS = 500;           // and is noise if it lasted less than this
const OPEN_ROOM_MM = 2000;          // wider than this across the path is a room, not a gap
export const CLAIM_MM = 650;        // an obstacle this near is reported: the camera's old stopping distance
export const CLAIM_HOLD_MS = 1000;  // once it has been that near this long (PROTOCOL.md section 6, 1)

// Distance from a point to the nearest edge of an axis-aligned obstacle, 0 inside it.
export const rangeTo = (o, x, y) => Math.hypot(Math.max(o.x0 - x, 0, x - o.x1), Math.max(o.y0 - y, 0, y - o.y1));

const wrap = (d) => ((d + 180) % 360 + 360) % 360 - 180;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const toRad = (deg) => (deg * Math.PI) / 180;

// Smallest non-zero range over a bearing window, degrees relative to straight ahead.
function minRange(scan, from, to) {
  let best = Infinity;
  for (let a = from; a <= to; a++) { const r = scan[((a % 360) + 360) % 360]; if (r && r < best) best = r; }
  return best;
}

export class Sim {
  constructor({ config = CONFIG, seed = 7 } = {}) {
    this.config = { ...config };
    this.jitter = makeJitter(seed);
    this.grid = new Grid();
    this.x = config.wall_target_mm; this.y = config.wall_target_mm; this.heading = 0;
    this.v = 0; this.w = 0;
    this.mode = 'idle';
    this.cmd = { v: 0, w: 0, at: -Infinity };   // last teleop drive and when it arrived
    this.pinch = null;                          // { since, min, x, y, between } while inside a gap
    this.armed = true;                          // false after a pinch times out, until the gap opens
    this.named = new Set();                     // obstacles reported this run
    this.nearby = new Map();                    // obstacle -> t it came within CLAIM_MM. Non-empty is `measuring`
    this.widths = [];                           // [x, y, mm] of every width verdict this run
    this.seq = 0;
    this.space = '';
    this.lastMap = -Infinity;
    this.loop = null;                           // wall_follow: { x0, y0, travelled }
  }

  // --- commands ---
  drive(v, w, t) {
    this.cmd = { v: clamp(Number(v) || 0, -1, 1), w: clamp(Number(w) || 0, -1, 1), at: t };
    this.mode = 'teleop';
    this.loop = null;
  }
  stop() { this.mode = 'idle'; this.cmd = { v: 0, w: 0, at: -Infinity }; this.loop = null; }
  setMode(mode) {
    this.mode = mode;
    this.cmd = { v: 0, w: 0, at: -Infinity };
    this.loop = mode === 'wall_follow' ? { x0: this.x, y0: this.y, travelled: 0 } : null;
  }
  // "Forget the map and start mapping again from here": obstacles get named again and gaps measured again.
  clearMap() { this.grid.clear(); this.lastMap = -Infinity; this.named.clear(); this.nearby.clear(); this.widths = []; this.pinch = null; this.armed = true; }
  runStart(space) { this.space = space; this.named.clear(); this.widths = []; this.clearMap(); return [this.event({ kind: 'run_start' })]; }
  runStop() { const ev = this.event({ kind: 'run_stop' }); this.space = ''; return [ev]; }
  mark(label) { return [this.event({ kind: 'mark', label: String(label ?? 'mark'), x_mm: Math.round(this.x), y_mm: Math.round(this.y) })]; }

  event(fields) { return { type: 'event', t: this.t, seq: this.seq++, ...fields, space: this.space }; }

  // --- one 10 Hz frame ---
  step(t, dt) {
    this.t = t;
    const out = [];
    const scan = scanFrom(this.x, this.y, this.heading, this.jitter);
    this.grid.carve(this.x, this.y, this.heading, scan);

    // what the robot wants to do
    let v = 0, w = 0;
    if (this.mode === 'teleop') {
      if (t - this.cmd.at <= WATCHDOG_MS) { v = this.cmd.v; w = this.cmd.w; }
    } else if (this.mode === 'wall_follow') {
      ({ v, w } = this.follow(scan));
    }

    // every obstacle Scout has come near, reported once (section 6, 1). While one is being confirmed
    // Scout holds still, as it did for the camera: drive is accepted, the motors do not turn, and it
    // moves again on the tick the pin lands (section 6, 2).
    out.push(...this.claimNear(t));
    if (this.nearby.size > 0) { v = 0; w = 0; }

    // move, keeping the body out of every surface: a blocked move just does not happen
    this.heading = wrap(this.heading + w * W_MAX * dt);
    const stepMm = v * V_MAX * dt;
    if (stepMm) {
      const a = toRad(this.heading);
      const nx = this.x + Math.cos(a) * stepMm, ny = this.y + Math.sin(a) * stepMm;
      if (clearanceAt(nx, ny) >= ROBOT_R) {
        if (this.loop) this.loop.travelled += Math.abs(stepMm);
        this.x = nx; this.y = ny;
      }
    }
    this.v = v; this.w = w;

    // wall_follow ends when the loop closes (section 6, 5)
    if (this.loop && this.loop.travelled > 0.6 * 2 * (ROOM.w + ROOM.l) && Math.hypot(this.x - this.loop.x0, this.y - this.loop.y0) < 350) {
      this.mode = 'idle'; this.loop = null; this.v = 0; this.w = 0;
    }

    // clearance across the path, and the pinch it may open or close (section 6, 3)
    const clearance = this.clearance(scan);
    out.push(...this.pinchStep(t, clearance));

    out.push({
      type: 'telem', t, mode: this.mode, measuring: this.nearby.size > 0, lidar: true, scan, gaps: [],
      pose: true, x_mm: Math.round(this.x), y_mm: Math.round(this.y), heading_deg: Math.round(this.heading * 10) / 10,
      room: { w_mm: ROOM.w, l_mm: ROOM.l },
      clearance_mm: clearance.mm, bump: [0, 0], stuck: false, v: this.v, w: this.w,
    });
    if (t - this.lastMap >= 1000) { out.push(this.grid.frame(t)); this.lastMap = t; }
    return out;
  }

  // Every obstacle Scout has come near, reported once each whether or not it ever faced it: the
  // lidar sees all the way round. It arrives unnamed, because nothing on Scout can say what it is;
  // claim_near in pi/scout/mapping.py sends the same.
  claimNear(t) {
    const out = [];
    for (const o of OBSTACLES) {
      if (this.named.has(o)) continue;
      if (rangeTo(o, this.x, this.y) > CLAIM_MM) { this.nearby.delete(o); continue; }
      const since = this.nearby.get(o);
      if (since === undefined) { this.nearby.set(o, t); continue; }
      if (t - since < CLAIM_HOLD_MS) continue;
      this.named.add(o);
      this.nearby.delete(o);
      out.push(this.event({ kind: 'obstacle', label: 'unknown', confidence: 0, photo: '',
        x_mm: Math.round((o.x0 + o.x1) / 2), y_mm: Math.round((o.y0 + o.y1) / 2) }));
    }
    return out;
  }

  // Straight across the path: left return plus right return. Zero in an open room and when the
  // way ahead is blocked, as PROTOCOL.md section 6 says.
  clearance(scan) {
    const l = cast(this.x, this.y, toRad(this.heading + 90)), r = cast(this.x, this.y, toRad(this.heading - 90));
    const mm = Math.round(l.d + r.d);
    if (!isFinite(mm) || mm > OPEN_ROOM_MM || minRange(scan, -10, 10) < 400) return { mm: 0, between: 'unknown' };
    const obstacle = l.seg?.[4] || r.seg?.[4];
    // the middle of the gap, so the verdict lands between the two surfaces and not on Scout
    const a = toRad(this.heading + 90), off = (l.d - r.d) / 2;
    return { mm, between: obstacle ? 'wall-obstacle' : 'wall-wall', x: this.x + Math.cos(a) * off, y: this.y + Math.sin(a) * off };
  }

  // A pinch opens when a gap narrower than 1.4 times the limit is across the path, tracks the
  // narrowest reading, and closes when the gap opens or after PINCH_MS. One verdict per pinch:
  // after a timeout nothing reopens until the gap has actually opened.
  pinchStep(t, c) {
    const limit = this.config.width_limit_mm;
    const inside = c.mm > 0 && c.mm < 1.4 * limit;
    if (!inside) this.armed = true;
    if (inside && !this.pinch && this.armed) this.pinch = { since: t, min: c.mm, x: c.x, y: c.y, between: c.between };
    else if (inside && this.pinch && c.mm < this.pinch.min) Object.assign(this.pinch, { min: c.mm, x: c.x, y: c.y, between: c.between });
    if (!this.pinch || (inside && t - this.pinch.since < PINCH_MS)) return [];
    const p = this.pinch; this.pinch = null;
    if (inside) this.armed = false;
    if (t - p.since < PINCH_MIN_MS) return [];   // a blip while a corner swept past the rays
    // one verdict per pinch point: a like-sized gap already judged within 400 mm is the same gap
    if (this.widths.some(([x, y, mm]) => Math.hypot(x - p.x, y - p.y) < 400 && Math.abs(mm - p.min) < 0.2 * mm)) return [];
    this.widths.push([p.x, p.y, p.min]);
    return [this.event({ kind: p.min < limit ? 'width_fail' : 'width_pass', value: p.min, unit: 'mm', limit,
      between: p.between, x_mm: Math.round(p.x), y_mm: Math.round(p.y) })];
  }

  // Right-hand wall following from the scan alone: hold wall_target_mm off the wall on the right,
  // turn left in place when something is ahead, arc right when the wall falls away at a corner.
  follow(scan) {
    const { wall_target_mm: target, cruise } = this.config;
    const ahead = minRange(scan, -25, 25);
    const frontRight = minRange(scan, -60, -30);
    const right = minRange(scan, -100, -80);
    if (ahead < 480 || frontRight < 320) return { v: 0, w: 0.35 };
    if (right > 1200 && frontRight > 1200) return { v: cruise * 0.7, w: -0.35 };   // lost the wall: wrap the corner
    // distance error plus a heading term: at 45 degrees the wall reads 1.414 times farther when parallel
    const diag = minRange(scan, -50, -40);
    const dist = (right - target) / 400;
    const angle = isFinite(diag) ? (diag - right * 1.414) / 700 : 0;
    return { v: cruise, w: clamp(-dist - angle, -0.35, 0.35) };
  }
}
