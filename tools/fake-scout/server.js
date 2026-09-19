// Pretends to be Scout. Serves protocol v1 (docs/PROTOCOL.md) from an NDJSON run file, on a loop,
// and logs every command it receives. Usage: node server.js [run.ndjson]   (PORT=8080 by default)
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocket, WebSocketServer } from 'ws';

const here = path.dirname(fileURLToPath(import.meta.url));
const file = process.argv[2] ?? path.join(here, '../../data/runs/table-course.ndjson');
const port = Number(process.env.PORT ?? 8080);
const FW = 'fake-0.1.0';
const log = (...a) => console.log(new Date().toISOString().slice(11, 23), ...a);

const lines = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
const header = lines.find((l) => l.type === 'run') ?? {};
const frames = lines.filter((l) => l.type === 'telem' || l.type === 'event');
if (!frames.length) throw new Error(`no frames in ${file}`);

// --- state the real firmware keeps ---
const boot = Date.now();
const config = { slope_limit_deg: 4.76, width_limit_mm: 860, scale: 1.0, width_offset_mm: 45, ...header.config };
let run = { active: false, space: '' };
let lastTelem = frames.find((f) => f.type === 'telem') ?? {};

// --- playback: broadcast the file with its original timing, forever ---
const clients = new Set();
const eventCount = frames.filter((f) => f.type === 'event').length;
const span = frames.at(-1).t - frames[0].t + 1000; // one loop plus a 1 s gap
let i = 0, loop = 0;
function tick() {
  const f = frames[i];
  const out = { ...f, t: f.t + loop * span }; // t and seq keep rising across loops
  if (f.type === 'event') out.seq = f.seq + loop * eventCount;
  if (f.type === 'telem') lastTelem = out;
  const s = JSON.stringify(out);
  for (const c of clients) if (c.readyState === WebSocket.OPEN) c.send(s);
  i += 1;
  if (i === frames.length) { i = 0; loop += 1; setTimeout(tick, 1000); return; }
  setTimeout(tick, frames[i].t - f.t);
}
tick();

// --- commands: logged, with the 500 ms teleop watchdog simulated ---
let driveCount = 0, lastDrive = null, watchdog = null;
const SHORT = {
  forward: { cmd: 'drive', v: 0.5, w: 0 }, back: { cmd: 'drive', v: -0.5, w: 0 },
  left: { cmd: 'drive', v: 0, w: 0.5 }, right: { cmd: 'drive', v: 0, w: -0.5 },
  stop: { cmd: 'stop' }, beep: { cmd: 'beep' },
};
function command(c) {
  if (!c || typeof c.cmd !== 'string') return { ok: false, err: 'no cmd' };
  switch (c.cmd) {
    case 'drive':
      driveCount += 1; lastDrive = c;
      clearTimeout(watchdog);
      watchdog = setTimeout(() => log('watchdog: no drive for 500 ms, motors stopped'), 500);
      return { ok: true };
    case 'stop':
      clearTimeout(watchdog); watchdog = null;
      log('cmd stop (E-STOP), mode idle');
      return { ok: true };
    case 'mode':
      if (c.mode !== 'teleop' && c.mode !== 'idle') return { ok: false, err: `unknown mode ${c.mode}` };
      log('cmd', JSON.stringify(c));
      return { ok: true };
    case 'run':
      if (c.action === 'start') run = { active: true, space: String(c.space ?? '') };
      else if (c.action === 'stop') run = { ...run, active: false };
      else return { ok: false, err: `unknown action ${c.action}` };
      log('cmd', JSON.stringify(c));
      return { ok: true };
    case 'config':
      for (const k of ['slope_limit_deg', 'width_limit_mm', 'scale', 'width_offset_mm']) if (typeof c[k] === 'number') config[k] = c[k];
      log('cmd config ->', JSON.stringify(config));
      return { ok: true };
    case 'mark': case 'zero': case 'beep':
      log('cmd', JSON.stringify(c));
      return { ok: true };
    default:
      log('cmd unknown', JSON.stringify(c));
      return { ok: false, err: `unknown cmd ${c.cmd}` };
  }
}
setInterval(() => { // drive arrives at 10 Hz, so summarise it once a second
  if (driveCount) { log(`drive x${driveCount}  v=${lastDrive.v} w=${lastDrive.w}`); driveCount = 0; }
}, 1000);

const status = () => ({
  proto: 1, fw: FW, mode: lastTelem.mode ?? 'idle', measuring: !!lastTelem.measuring,
  run, config, uptime_ms: Date.now() - boot, heap: 181000, ip: '127.0.0.1',
});

// --- HTTP ---
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST', 'Access-Control-Allow-Headers': 'Content-Type' };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://scout');
  const json = (code, body) => { res.writeHead(code, { ...CORS, 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  if (req.method === 'OPTIONS') { res.writeHead(204, CORS); return res.end(); }
  if (url.pathname === '/status') return json(200, status());
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
    return fs.createReadStream(file).pipe(res);
  }
  json(404, { ok: false, err: 'not found' });
});

// --- WebSocket: frames out, commands in ---
const wss = new WebSocketServer({ server, path: '/ws' });
wss.on('connection', (ws) => {
  clients.add(ws);
  log(`ws client connected (${clients.size})`);
  ws.on('message', (m) => { try { command(JSON.parse(m)); } catch { log('ws: bad json'); } });
  ws.on('close', () => { clients.delete(ws); log(`ws client left (${clients.size})`); });
});

server.listen(port, () => log(`fake scout: http://localhost:${port}  ws://localhost:${port}/ws  playing ${path.relative(process.cwd(), file)}`));
