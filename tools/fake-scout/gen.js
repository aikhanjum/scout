// Writes one scripted room survey in protocol v2 NDJSON (docs/PROTOCOL.md section 7): the room
// and ray caster from sim.js, a robot wall-following the perimeter along fixed waypoints, and an
// occupancy grid built from every scan. Deterministic. Run with `npm run gen`.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CLAIM_HOLD_MS, CLAIM_MM, CONFIG, Grid, OBSTACLES, ROOM, makeJitter, rangeTo, scanFrom } from './sim.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] ?? path.join(here, '../../data/runs/room-scan.ndjson');

const HZ = 10;
const T0 = 1000;
const SPACE = 'E5 room 2024';
const grid = new Grid();
const mapFrame = (t) => grid.frame(t);

// --- the route ---------------------------------------------------------------
// Wall-follow the perimeter clockwise at wall_target_mm, going round what stands in the lane.
// Obstacles are reported as the route comes near them (the same rule as the live robot below);
// `width` names the obstacle whose slot against the wall is measured at that waypoint.
const M = CONFIG.wall_target_mm;
const route = [
  { x: M, y: M },
  { x: 1450, y: M },                                             // up to the chair
  { x: 1450, y: 1150 },                                          // round it
  { x: 2900, y: 1150 },
  { x: 2900, y: M },                                             // back down to the wall
  { x: ROOM.w - M, y: M },
  { x: ROOM.w - M, y: 1150 },                                    // turn north, the ramp is ahead
  { x: 3150, y: 1150 },                                          // round the ramp
  { x: 3150, y: 2600 },
  { x: ROOM.w - M, y: 2600 },
  { x: ROOM.w - M, y: 3000, width: 2 },                          // the bin pinches against the wall
  { x: ROOM.w - M, y: ROOM.l - M },
  { x: M, y: ROOM.l - M },
  { x: M, y: M },
];

// --- run ---------------------------------------------------------------------
const jitter = makeJitter(7);
const SPEED = 420;                       // mm/s at cruise 0.4
const lines = [{ type: 'run', space: SPACE, fw: 'fake-0.2.0', started_t: T0, started_at: '2026-09-19T12:00:00.000Z', config: CONFIG }];
let t = T0, seq = 0, lastMap = -Infinity;

function emit(ev) { lines.push({ type: 'event', t, seq: seq++, ...ev, space: SPACE }); }

// The live robot's rule (sim.js claimNear): an obstacle within CLAIM_MM for CLAIM_HOLD_MS is
// reported once, unnamed, wherever it lies round the robot.
const nearby = new Map(), reported = new Set();
function claim(x, y) {
  for (const o of OBSTACLES) {
    if (reported.has(o)) continue;
    if (rangeTo(o, x, y) > CLAIM_MM) { nearby.delete(o); continue; }
    if (!nearby.has(o)) { nearby.set(o, t); continue; }
    if (t - nearby.get(o) < CLAIM_HOLD_MS) continue;
    reported.add(o);
    emit({ kind: 'obstacle', label: 'unknown', confidence: 0, photo: '',
           x_mm: Math.round((o.x0 + o.x1) / 2), y_mm: Math.round((o.y0 + o.y1) / 2) });
  }
}

function frame(x, y, heading, v, w) {
  const scan = scanFrom(x, y, heading, jitter);
  grid.carve(x, y, heading, scan);
  // narrowest gap across the path: the pair of returns bounding the way ahead
  const left = scan[85] || 0, right = scan[275] || 0;
  lines.push({
    // gaps: [] on purpose. The gap finder is pi/scout/gaps.py and is not ported to JS; the
    // simulator supplies the rotation and the real robot supplies the openings found in it.
    type: 'telem', t, mode: 'wall_follow', measuring: false, lidar: true, scan, gaps: [],
    pose: true, x_mm: Math.round(x), y_mm: Math.round(y), heading_deg: Math.round(heading * 10) / 10,
    room: { w_mm: ROOM.w, l_mm: ROOM.l },
    clearance_mm: left && right ? left + right : 0,
    bump: [0, 0], stuck: false, v, w,
  });
  // one map per 10 s, the cadence PROTOCOL.md section 7 gives a recorder: enough to watch the
  // room fill in on replay, without the grid dominating the file
  if (t - lastMap >= 10000) { lines.push(mapFrame(t)); lastMap = t; }
  claim(x, y);
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
    frame(px, py, heading + (delta * s) / turnSteps, 0, Math.sign(delta) * 0.35);
  }
  heading = want;

  const steps = Math.max(1, Math.round((dist / SPEED) * HZ));
  for (let s = 1; s <= steps; s++) {
    frame(px + ((wp.x - px) * s) / steps, py + ((wp.y - py) * s) / steps, heading, CONFIG.cruise, 0);
  }
  px = wp.x; py = wp.y;

  if (wp.width != null) {
    const o = OBSTACLES[wp.width];
    const gap = Math.round(ROOM.w - o.x1);          // the slot between it and the right wall
    emit({ kind: gap < CONFIG.width_limit_mm ? 'width_fail' : 'width_pass', value: gap, unit: 'mm',
           limit: CONFIG.width_limit_mm, between: 'wall-obstacle',
           x_mm: Math.round((o.x1 + ROOM.w) / 2), y_mm: Math.round((o.y0 + o.y1) / 2) });
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
console.log(`room ${ROOM.w}x${ROOM.l} mm, grid ${grid.w}x${grid.h} at ${grid.frame(0).cell_mm} mm`);
