// The link to Scout. One source at a time: a live WebSocket, or a replay file played by t.
// Pushes frames into the store, keeps the link flag honest, speaks verdicts, records.
import { useStore, type Source } from './store';
import { PROTO, widthRule, type Command, type Frame, type RunHeader, type ScoutEvent, type Status } from './protocol';
import { SPOKEN, speak, verdict } from './verdict';

const LINK_TIMEOUT_MS = 2000;
const RECONNECT_MS = 1000;
const REC_MAP_MS = 10000;   // one map frame per 10 s into a recording (PROTOCOL.md section 7)

let ws: WebSocket | null = null;
let generation = 0; // bumped by every connect(); callbacks from an older generation are ignored
let watchdog = 0, reconnectTimer = 0, replayTimer = 0;
let rec: Frame[] | null = null;
let recLastMap = -Infinity;

const store = () => useStore.getState();

export function connect(source: Source) {
  generation += 1;
  clearTimeout(watchdog); clearTimeout(reconnectTimer); clearTimeout(replayTimer);
  if (ws) { ws.onclose = null; ws.onmessage = null; ws.close(); ws = null; }
  store().set({ source, link: 'down', detail: '', telem: null, map: null, events: [], baseUrl: '' });
  if (source.kind === 'live') openSocket(source.url, generation);
  else void playFile(source, generation);
}

export function send(cmd: Command) {
  if (store().source.kind !== 'live') return; // replay: commands go nowhere
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(cmd));
}

// Limits. Sent on every connect. robot_width_mm, wall_target_mm and cruise are robot
// calibration, so the dashboard never sends them.
export function sendConfig() {
  const { rules } = store();
  send({ cmd: 'config', width_limit_mm: widthRule(rules)?.limit ?? 860 });
}

export function startRecording() { rec = []; recLastMap = -Infinity; store().set({ recording: true }); }

// Returns the recording as NDJSON (PROTOCOL.md section 7), or null if there was nothing.
export function stopRecording(space: string): string | null {
  const frames = rec; rec = null;
  store().set({ recording: false });
  if (!frames?.length) return null;
  const { detail, rules } = store();
  const header: RunHeader = {
    type: 'run', space, fw: detail || 'unknown', started_t: frames[0].t,
    config: { width_limit_mm: widthRule(rules)?.limit ?? 860 },
  };
  return [header, ...frames].map((x) => JSON.stringify(x)).join('\n') + '\n';
}

// --- live ---

function httpOrigin(wsUrl: string) {
  const u = new URL(wsUrl);
  u.protocol = u.protocol === 'wss:' ? 'https:' : 'http:';
  u.pathname = ''; u.search = '';
  return u.toString().replace(/\/$/, '');
}

function openSocket(url: string, gen: number) {
  let s: WebSocket;
  try { s = new WebSocket(url); } catch { store().set({ detail: 'bad URL' }); return; }
  ws = s;
  store().set({ detail: 'connecting' });
  s.onopen = () => {
    if (gen !== generation) return;
    store().set({ detail: 'connected', baseUrl: httpOrigin(url) });
    sendConfig();
    void fetchStatus(url, gen);
  };
  s.onmessage = (e) => { if (gen !== generation) return; try { handleFrame(JSON.parse(e.data)); } catch { /* not a frame, ignore */ } };
  s.onclose = () => {
    if (gen !== generation) return;
    ws = null; linkDown(); store().set({ detail: 'reconnecting' });
    reconnectTimer = window.setTimeout(() => { if (gen === generation) openSocket(url, gen); }, RECONNECT_MS);
  };
}

async function fetchStatus(wsUrl: string, gen: number) {
  try {
    const st: Status = await (await fetch(`${httpOrigin(wsUrl)}/status`)).json();
    if (gen !== generation) return;
    const d = st.devices ?? { esp32: false, lidar: false, camera: false };
    const missing = Object.entries(d).filter(([, ok]) => !ok).map(([k]) => k);
    store().set({
      detail: `fw ${st.fw}  ${st.ip}`
        + (missing.length ? `  no ${missing.join('/')}` : '')
        + (st.proto !== PROTO ? `  PROTO ${st.proto} (expected ${PROTO})` : ''),
    });
  } catch { /* status is a nicety, telemetry is the truth */ }
}

function linkDown() { store().set({ link: 'down' }); }

function handleFrame(f: Frame) {
  if (f.type === 'telem') {
    store().set({ telem: f, link: 'up' });
    clearTimeout(watchdog);
    watchdog = window.setTimeout(linkDown, LINK_TIMEOUT_MS);
    rec?.push(f);
  } else if (f.type === 'map') {
    store().set({ map: f });
    if (rec && f.t - recLastMap >= REC_MAP_MS) { rec.push(f); recLastMap = f.t; }
  } else if (f.type === 'event') {
    store().addEvent(f);
    if (store().voice && SPOKEN.has(f.kind)) speak(verdict(f));
    rec?.push(f);
  }
}

// --- replay: same loop as tools/fake-scout, in the browser, so a replay needs no server ---

async function playFile(source: Extract<Source, { kind: 'replay' }>, gen: number) {
  let text = source.text;
  if (text == null) {
    try {
      const r = await fetch(`/runs/${source.name}`);
      if (!r.ok) throw new Error(String(r.status));
      text = await r.text();
    } catch { store().set({ detail: `cannot load runs/${source.name}` }); return; }
  }
  if (gen !== generation) return;
  const lines: (Frame | RunHeader)[] = [];
  for (const l of text.split('\n')) { if (l.trim()) try { lines.push(JSON.parse(l)); } catch { /* skip bad line */ } }
  const header = lines.find((l): l is RunHeader => l.type === 'run');
  const frames = lines.filter((l): l is Frame => l.type === 'telem' || l.type === 'event' || l.type === 'map');
  if (!frames.length) { store().set({ detail: `${source.name}: no frames` }); return; }
  store().set({ detail: `${source.name}  ${header?.space ?? ''}` });

  const eventCount = frames.filter((f) => f.type === 'event').length;
  const span = frames[frames.length - 1].t - frames[0].t + 1000; // one loop plus a 1 s gap
  let i = 0, loop = 0;
  const step = () => {
    if (gen !== generation) return;
    const f = frames[i];
    const out: Frame = { ...f, t: f.t + loop * span }; // t and seq keep rising across loops
    if (out.type === 'event') out.seq = (f as ScoutEvent).seq + loop * eventCount;
    handleFrame(out);
    i += 1;
    if (i === frames.length) { i = 0; loop += 1; replayTimer = window.setTimeout(step, 1000); return; }
    replayTimer = window.setTimeout(step, Math.max(0, frames[i].t - f.t));
  };
  step();
}
