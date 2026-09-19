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
const CONFIG = { slope_limit_deg: 4.76, width_limit_mm: 860, scale: 0.25, width_offset_mm: 45 };
const WIDTH_LIMIT = CONFIG.width_limit_mm * CONFIG.scale; // 215 mm on the 1:4 course

// side = distance from each sonar to the wall. The 400 mm lane reads about 178 per side,
// the 190 mm FAIL gate about 72, the 250 mm PASS gate about 102 (width = right + left + offset).
const LANE = 178, GATE_FAIL = 72, GATE_PASS = 102;

// The run, as segments. pitch is [start, end] over the segment. Events fire at the segment's end.
const segments = [
  { secs: 1.0, mode: 'idle',   v: 0,   pitch: [0, 0],     side: LANE, events: [{ kind: 'run_start' }] },
  { secs: 3.0, mode: 'teleop', v: 0.4, pitch: [0, 0],     side: LANE },
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

const lines = [{ type: 'run', space: SPACE, fw: 'fake-0.1.0', started_t: T0, config: CONFIG }];
let t = T0, seq = 0, yaw = 0;
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
console.log(`wrote ${path.relative(process.cwd(), out)}: ${lines.length - 1} frames, ${seq} events, ${(t - T0) / 1000} s`);
