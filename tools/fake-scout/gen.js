// Writes one scripted table-course run in protocol v1 NDJSON (docs/PROTOCOL.md, section 6).
// Deterministic: the same file every time. Run with `npm run gen`.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] ?? path.join(here, '../../data/runs/table-course.ndjson');

const HZ = 10;
const T0 = 1000;
const SPACE = 'Table course';
const CONFIG = { slope_limit_deg: 4.76, width_limit_mm: 860, scale: 0.25, width_offset_mm: 0 };
const WIDTH_LIMIT = CONFIG.width_limit_mm * CONFIG.scale; // 215 mm on the 1:4 course

// side = distance from the lidar to each wall, so width = right + left. The offset is 0 here
// so the scan frame's geometry and telem.width_mm are the same numbers: a 400 mm lane, a
// 190 mm FAIL gate, a 250 mm PASS gate.
const LANE = 200, GATE_FAIL = 95, GATE_PASS = 125;

// --- the world the scan frame is cast against ------------------------------
// A closed 1:4 corridor in Scout's frame: +X is left, +Y is ahead. Side walls at +-side,
// an end wall 2 m ahead and 2.5 m behind. A gate is a constriction across the lane, drawn
// ahead of Scout while it approaches one.
const SCAN_HZ = 2;
const SCAN_STEP_DEG = 1.2;          // about what the A2M8 gives in express mode
const AHEAD_END = 2000, BEHIND_END = 2500;
const SPEED_MM_S = 250;             // how fast the scripted rover covers ground at v = 1
const GATE_DEPTH = 150;
const GATE_VISIBLE_MM = 1500;

function rect(x0, y0, x1, y1) {
  return [[[x0, y0], [x1, y0]], [[x1, y0], [x1, y1]], [[x1, y1], [x0, y1]], [[x0, y1], [x0, y0]]];
}

// range along a bearing, 0 for no hit
function cast(segs, deg) {
  const th = (deg * Math.PI) / 180;
  const dx = Math.sin(th), dy = Math.cos(th);
  let best = 0;
  for (const [[x1, y1], [x2, y2]] of segs) {
    const ex = x2 - x1, ey = y2 - y1;
    const den = dx * ey - ex * dy;
    if (Math.abs(den) < 1e-9) continue;
    const t = (x1 * ey - ex * y1) / den;
    const u = (dy * x1 - dx * y1) / den;
    if (t > 0 && u >= 0 && u <= 1 && (best === 0 || t < best)) best = t;
  }
  return best;
}

const bearing = (x, y) => r1((Math.atan2(x, y) * 180) / Math.PI);

// The room Scout is in right now, plus the gate ahead if one is close enough.
function world(side, gate) {
  const segs = [...rect(-side, -BEHIND_END, side, AHEAD_END)];
  if (gate) {
    const half = gate.width / 2;
    segs.push(...rect(half, gate.dist, side, gate.dist + GATE_DEPTH));     // left post
    segs.push(...rect(-side, gate.dist, -half, gate.dist + GATE_DEPTH));   // right post
  }
  return segs;
}

// A stretch of wall that returns nothing, so replay also shows what an unverified gap
// looks like: an arc of no-returns that could be an opening or could be matte black.
const BLIND = { from: 52, to: 74 };   // bearings, on the left wall

// The run, as segments. pitch is [start, end] over the segment. Events fire at the segment's end.
const segments = [
  { secs: 1.0, mode: 'idle',   v: 0,   pitch: [0, 0],     side: LANE, events: [{ kind: 'run_start' }] },
  { secs: 3.0, mode: 'teleop', v: 0.4, pitch: [0, 0],     side: LANE, blind: true },   // a stretch of wall that returns nothing
  { secs: 0.8, mode: 'teleop', v: 0.4, pitch: [0, 7.1],   side: LANE },                 // up the ramp
  { secs: 1.5, mode: 'teleop', v: 0,   pitch: [7.1, 7.1], side: LANE, measuring: true,  // stop and measure
    events: [{ kind: 'slope_fail', value: 7.1, unit: 'deg', limit: CONFIG.slope_limit_deg, scale: 1.0 }] },
  { secs: 1.0, mode: 'teleop', v: 0.4, pitch: [7.1, 7.1], side: LANE },
  { secs: 0.5, mode: 'teleop', v: 0.4, pitch: [7.1, 0],   side: LANE },                 // onto the landing
  { secs: 1.5, mode: 'teleop', v: 0.4, pitch: [0, 0],     side: LANE },
  { secs: 0.8, mode: 'teleop', v: 0.4, pitch: [0, 0],     side: GATE_FAIL,              // 190 mm gate
    events: [{ kind: 'width_fail', unit: 'mm', limit: WIDTH_LIMIT, scale: CONFIG.scale }] },
  { secs: 1.5, mode: 'teleop', v: 0.4, pitch: [0, 0],     side: LANE, events: [{ kind: 'mark', label: 'chair leg' }] },
  { secs: 0.8, mode: 'teleop', v: 0.4, pitch: [0, 0],     side: GATE_PASS,              // 250 mm gate
    events: [{ kind: 'width_pass', unit: 'mm', limit: WIDTH_LIMIT, scale: CONFIG.scale }] },
  { secs: 1.2, mode: 'teleop', v: 0.4, pitch: [0, 0],     side: LANE },
  { secs: 1.0, mode: 'idle',   v: 0,   pitch: [0, 0],     side: LANE, events: [{ kind: 'run_stop' }] },
];

// seeded noise so the file is stable
let seed = 42;
const noise = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32) - 0.5;
const r1 = (x) => Math.round(x * 10) / 10;

// when each segment starts, so an approach can tell how far the next gate is
const starts = [];
{
  let acc = T0;
  for (const sg of segments) { starts.push(acc); acc += Math.round(sg.secs * HZ) * (1000 / HZ); }
}
const gateAt = segments.map((sg, i) => (sg.side < LANE ? { t: starts[i], width: sg.side * 2 } : null))
  .filter(Boolean);

// Only a gate narrower than the lane Scout is in can be drawn: this world has one
// corridor width per frame, so a wider gate ahead has nothing to stand on and would
// mean claiming a gap the points do not show.
function gateAhead(now, side) {
  for (const g of gateAt) {
    const dist = ((g.t - now) / 1000) * SPEED_MM_S;
    if (dist > 0 && dist < GATE_VISIBLE_MM && g.width < 2 * side) {
      return { dist: Math.round(dist), width: g.width };
    }
  }
  return null;
}

function scanFrame(t, side, blind) {
  const gate = gateAhead(t, side);
  const segs = world(side, gate);
  const pts = [];
  for (let a = -180; a < 180; a += SCAN_STEP_DEG) {
    const deg = r1(a);
    const blinded = blind && deg >= BLIND.from && deg <= BLIND.to;
    pts.push([deg, blinded ? 0 : Math.round(cast(segs, deg))]);
  }
  const gaps = [];
  if (gate) {
    const half = gate.width / 2;
    const r = Math.round(Math.hypot(half, gate.dist));
    gaps.push({ a0: bearing(-half, gate.dist), mm0: r, a1: bearing(half, gate.dist), mm1: r,
      width_mm: gate.width, span_deg: r1(2 * bearing(half, gate.dist)), evidence: 'see_through' });
  }
  if (blind) {
    const m0 = Math.round(cast(segs, BLIND.from - SCAN_STEP_DEG));
    const m1 = Math.round(cast(segs, BLIND.to + SCAN_STEP_DEG));
    const th = ((BLIND.to - BLIND.from) * Math.PI) / 180;
    gaps.push({ a0: BLIND.from - SCAN_STEP_DEG, mm0: m0, a1: BLIND.to + SCAN_STEP_DEG, mm1: m1,
      width_mm: Math.round(Math.sqrt(m0 * m0 + m1 * m1 - 2 * m0 * m1 * Math.cos(th))),
      span_deg: r1(BLIND.to - BLIND.from + 2 * SCAN_STEP_DEG), evidence: 'unverified' });
  }
  return { type: 'scan', t, hz: 9.6, mode: 'express', pts, gaps };
}

const lines = [{ type: 'run', space: SPACE, fw: 'fake-0.1.0', started_t: T0, config: CONFIG }];
let t = T0, seq = 0, yaw = 0, tick = 0;
for (const sg of segments) {
  let minWidth = Infinity;
  const n = Math.round(sg.secs * HZ);
  for (let i = 0; i < n; i++) {
    const k = i / n;
    const jitter = sg.v === 0 ? 0.1 : 0.6; // the IMU is quiet when still, noisy when moving
    const right = sg.side + noise() * 6, left = sg.side + noise() * 6;
    const width = Math.round(right + left + CONFIG.width_offset_mm);
    minWidth = Math.min(minWidth, width);
    yaw += 0.005 + noise() * 0.1; // slow gyro drift
    lines.push({
      type: 'telem', t, mode: sg.mode, measuring: !!sg.measuring,
      pitch_deg: r1(sg.pitch[0] + (sg.pitch[1] - sg.pitch[0]) * k + noise() * jitter),
      roll_deg: r1(noise() * 0.4), yaw_deg: r1(yaw),
      sweep: [{ a: -90, mm: Math.round(right) }, { a: 90, mm: Math.round(left) }],
      width_mm: width, bump: [0, 0], stuck: false, v: sg.v, w: 0,
    });
    if (tick % (HZ / SCAN_HZ) === 0) lines.push(scanFrame(t, sg.side, !!sg.blind));
    tick += 1;
    t += 1000 / HZ;
  }
  for (const ev of sg.events ?? []) {
    const e = { type: 'event', t, seq: seq++, ...ev, space: SPACE };
    if (ev.kind.startsWith('width') && e.value == null) e.value = minWidth;
    lines.push(e);
  }
}

fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, lines.map((l) => JSON.stringify(l)).join('\n') + '\n');
const scans = lines.filter((l) => l.type === 'scan').length;
console.log(`wrote ${path.relative(process.cwd(), out)}: ${lines.length - 1} frames (${scans} scan), ${seq} events, ${(t - T0) / 1000} s`);
