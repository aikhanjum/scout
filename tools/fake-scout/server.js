// Pretends to be Scout. Serves protocol v2 (docs/PROTOCOL.md) on port 8080 and logs every command.
//
//   node server.js                 a live robot in the simulated room (sim.js). It obeys drive, stop,
//                                  mode, run, mark and map clear, and starts off wall-following.
//   node server.js run.ndjson      play that file on a loop instead. Commands are logged and ignored.
//
// PORT=8080 by default.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocket, WebSocketServer } from 'ws';
import { CONFIG, Sim } from './sim.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const file = process.argv[2] ? path.resolve(process.argv[2]) : null;
const port = Number(process.env.PORT ?? 8080);
const FW = 'fake-0.3.0';
const HZ = 10;
const log = (...a) => console.log(new Date().toISOString().slice(11, 23), ...a);

const boot = Date.now();
const now = () => Date.now() - boot;
const clients = new Set();
function broadcast(frame) {
  const s = JSON.stringify(frame);
  for (const c of clients) if (c.readyState === WebSocket.OPEN) c.send(s);
}

// --- the run buffer behind /runs/latest (PROTOCOL.md section 7) ---
// Every event, telemetry at 2 Hz, and only the newest map frame. Cleared by `run start`.
let run = { active: false, space: '' };
let buffer = [], lastMap = null, lastTelem = {}, telemCount = 0, started = { t: 0, at: new Date().toISOString() };
function record(f) {
  if (f.type === 'telem') { lastTelem = f; if (telemCount++ % (HZ / 2) === 0) buffer.push(f); }
  else if (f.type === 'map') lastMap = f;
  else buffer.push(f);
  if (buffer.length > 8000) buffer = buffer.filter((x, i) => x.type === 'event' || i % 2 === 0);   // old telemetry goes first
}
const runFile = () => {
  const header = { type: 'run', space: run.space, fw: FW, started_t: started.t, started_at: started.at, config };
  return [header, ...buffer, ...(lastMap ? [lastMap] : [])].map((x) => JSON.stringify(x)).join('\n') + '\n';
};

// --- the robot: a live simulation, or a file on a loop ---
let config = { ...CONFIG };
let robot;   // { command(c) -> reply | undefined, status() -> { mode, measuring, camera } }

if (file) {
  // Playback: the file with its original timing, forever. t and seq keep rising across loops.
  const lines = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  const header = lines.find((l) => l.type === 'run') ?? {};
  const frames = lines.filter((l) => l.type === 'telem' || l.type === 'event' || l.type === 'map');
  if (!frames.length) throw new Error(`no frames in ${file}`);
  config = { ...config, ...header.config };
  const eventCount = frames.filter((f) => f.type === 'event').length;
  const span = frames.at(-1).t - frames[0].t + 1000; // one loop plus a 1 s gap
  let i = 0, loop = 0;
  const tick = () => {
    const f = frames[i];
    const out = { ...f, t: f.t + loop * span };
    if (f.type === 'event') out.seq = f.seq + loop * eventCount;
    record(out); broadcast(out);
    i += 1;
    if (i === frames.length) { i = 0; loop += 1; setTimeout(tick, 1000); return; }
    setTimeout(tick, frames[i].t - f.t);
  };
  tick();
  robot = {
    command: (c) => { if (c.cmd === 'map') log('cmd map clear (playback keeps rolling)'); },
    status: () => ({ mode: lastTelem.mode ?? 'idle', measuring: !!lastTelem.measuring, camera: false }),
  };
  log(`playing ${path.relative(process.cwd(), file)}: commands are logged, not obeyed`);
} else {
  // Live: the simulated robot, stepped at 10 Hz. It starts roaming so a fresh dashboard sees a room
  // draw itself; a key press takes over.
  const sim = new Sim({ config });
  sim.setMode('wall_follow');
  let last = now(), lastMode = sim.mode;
  setInterval(() => {
    const t = now(), dt = Math.min(0.2, Math.max(0.05, (t - last) / 1000)); last = t;
    for (const f of sim.step(t, dt)) {
      if (f.type === 'event') log('event', f.kind, f.label ?? (f.value != null ? `${f.value} mm` : ''), f.x_mm != null ? `@ ${f.x_mm},${f.y_mm}` : '');
      record(f); broadcast(f);
    }
    if (sim.mode !== lastMode) { log(`mode ${sim.mode}`); lastMode = sim.mode; }
  }, 1000 / HZ);
  const emit = (events) => { for (const e of events) { log('event', e.kind, e.label ?? ''); record(e); broadcast(e); } };
  robot = {
    command: (c) => {
      switch (c.cmd) {
        case 'drive': sim.drive(c.v, c.w, now()); return;   // the motors stay stopped while measuring (section 5)
        case 'stop': sim.stop(); return;
        case 'mode': sim.setMode(c.mode); return;
        case 'run': if (c.action === 'start') emit(sim.runStart(run.space)); else emit(sim.runStop()); return;
        case 'mark': emit(sim.mark(c.label)); return;
        case 'map': sim.clearMap(); return;
        case 'config': sim.config = config; return;
      }
    },
    status: () => ({ mode: sim.mode, measuring: sim.nearby.size > 0, camera: false }),
  };
  log('live simulation: drive it from the dashboard');
}

// --- commands (PROTOCOL.md section 5): validated here, then handed to the robot ---
let driveCount = 0, lastDrive = null;
const SHORT = {
  forward: { cmd: 'drive', v: 0.5, w: 0 }, back: { cmd: 'drive', v: -0.5, w: 0 },
  left: { cmd: 'drive', v: 0, w: 0.5 }, right: { cmd: 'drive', v: 0, w: -0.5 },
  stop: { cmd: 'stop' }, beep: { cmd: 'beep' }, roam: { cmd: 'mode', mode: 'wall_follow' },
};
const MODES = ['idle', 'teleop', 'wall_follow'];
function command(c) {
  if (!c || typeof c.cmd !== 'string') return { ok: false, err: 'no cmd' };
  switch (c.cmd) {
    case 'drive':
      if (typeof c.v !== 'number' || typeof c.w !== 'number') return { ok: false, err: 'drive needs v and w' };
      driveCount += 1; lastDrive = c;
      break;
    case 'stop':
      log('cmd stop (E-STOP), mode idle');
      break;
    case 'mode':
      if (!MODES.includes(c.mode)) return { ok: false, err: `unknown mode ${c.mode}` };
      log('cmd', JSON.stringify(c));
      break;
    case 'run':
      if (c.action === 'start') { run = { active: true, space: String(c.space ?? '') }; buffer = []; lastMap = null; started = { t: now(), at: new Date().toISOString() }; }
      else if (c.action === 'stop') run = { ...run, active: false };
      else return { ok: false, err: `unknown action ${c.action}` };
      log('cmd', JSON.stringify(c));
      break;
    case 'map':
      if (c.action !== 'clear') return { ok: false, err: `unknown action ${c.action}` };
      log('cmd map clear');
      break;
    case 'config':
      for (const k of ['width_limit_mm', 'robot_width_mm', 'wall_target_mm', 'cruise']) if (typeof c[k] === 'number') config[k] = c[k];
      log('cmd config ->', JSON.stringify(config));
      break;
    case 'mark': case 'beep':
      log('cmd', JSON.stringify(c));
      break;
    default:
      log('cmd unknown', JSON.stringify(c));
      return { ok: false, err: `unknown cmd ${c.cmd}` };
  }
  robot.command(c);
  return { ok: true };
}
setInterval(() => { // drive arrives at 10 Hz, so summarise it once a second
  if (driveCount) { log(`drive x${driveCount}  v=${lastDrive.v} w=${lastDrive.w}`); driveCount = 0; }
}, 1000);

const status = () => {
  const r = robot.status();
  return {
    proto: 2, fw: FW, mode: r.mode, measuring: r.measuring, run,
    devices: { esp32: true, motor: true, lidar: true, camera: r.camera }, config,
    uptime_ms: now(), ip: '127.0.0.1',
  };
};

// --- HTTP ---
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST', 'Access-Control-Allow-Headers': 'Content-Type' };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://scout');
  const json = (code, body) => { res.writeHead(code, { ...CORS, 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  if (req.method === 'OPTIONS') { res.writeHead(204, CORS); return res.end(); }
  if (url.pathname === '/status') return json(200, status());
  if (url.pathname === '/map') return lastMap ? json(200, lastMap) : json(404, { ok: false, err: 'no map yet' });
  // no real camera here, so every photo id is a miss. The dashboard must cope (PROTOCOL.md section 6).
  if (url.pathname.startsWith('/photo/')) return json(404, { ok: false, err: 'no photo' });
  if (url.pathname === '/cmd' && req.method === 'GET') {
    const c = SHORT[url.searchParams.get('c')];
    return c ? json(200, command(c)) : json(400, { ok: false, err: 'unknown c' });
  }
  if (url.pathname === '/cmd' && req.method === 'POST') {
    let body = '';
    for await (const chunk of req) body += chunk;
    let c;
    try { c = JSON.parse(body); } catch { return json(400, { ok: false, err: 'bad json' }); }
    return json(200, command(c));
  }
  if (url.pathname === '/runs/latest') {
    res.writeHead(200, { ...CORS, 'Content-Type': 'application/x-ndjson' });
    return res.end(runFile());   // the buffer, in both modes: what this connection could have seen so far
  }
  json(404, { ok: false, err: 'not found' });
});

// --- WebSocket: frames out, commands in ---
const wss = new WebSocketServer({ server, path: '/ws' });
wss.on('connection', (ws) => {
  clients.add(ws);
  log(`ws client connected (${clients.size})`);
  if (lastMap) ws.send(JSON.stringify(lastMap));   // one map on connect, so the view is never blank
  ws.on('message', (m) => { try { command(JSON.parse(m)); } catch { log('ws: bad json'); } });
  ws.on('close', () => { clients.delete(ws); log(`ws client left (${clients.size})`); });
});

server.listen(port, () => log(`fake scout: http://localhost:${port}  ws://localhost:${port}/ws`));
