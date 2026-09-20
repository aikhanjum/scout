// The link to Scout. One source at a time: a live WebSocket, or a replay file played by t.
// Pushes frames into the store, keeps the link flag honest, speaks verdicts, records.
import { NO_RUN, useStore, type Source } from './store';
import { PROTO, widthRule, type Command, type Frame, type RunHeader, type ScoutEvent, type Status } from './protocol';
import { SPOKEN, speak, verdict } from './verdict';
import { telemRow, type TelemRow } from './export';

const LINK_TIMEOUT_MS = 2000;
const RECONNECT_MS = 1000;
const REC_MAP_MS = 10000;   // one map frame per 10 s into a recording (PROTOCOL.md section 7)
const LOG_MAX = 200000;     // telemetry rows kept for the tables: over five hours at 10 Hz

let ws: WebSocket | null = null;
let generation = 0; // bumped by every connect(); callbacks from an older generation are ignored
let watchdog = 0, reconnectTimer = 0, replayTimer = 0;
let rec: Frame[] | null = null;
let lastRecording: { space: string; text: string } | null = null;   // the run last stopped, for the Download menu
let recStartedAt = ''; // wall clock when REC was pressed: the run header's started_at
let recLastMap = -Infinity;

// What the Download menu hands out: every event and every telemetry frame (minus its scan) since
// the run started, or since connecting when no run was. Always on, unlike the recorder, because
// without the scans it is small.
let eventLog: ScoutEvent[] = [], telemLog: TelemRow[] = [];
let fileStartedAt = '';   // a replay's own wall clock, from its run header. A report made from a
                          // replay carries the date the run happened, not the date it was played.
export const sessionEvents = () => eventLog;
export const sessionTelemetry = () => telemLog;
export const sessionRecording = () => lastRecording;

const store = () => useStore.getState();

// Wipe the board. Scout's `map clear` drops its grid, its room frame and its audit together
// (reset_map in pi/scout/server.py), so every pin still on screen came from state the robot has
// just thrown away, and it will hand out the same chair again as it re-discovers it. The map, the
// trail, the pins and the rows behind the Download menu go together or not at all. The recorder is
// left alone: an .ndjson is a faithful log of the wire, not of the board.
export function clearBoard() {
  eventLog = []; telemLog = [];
  store().set({ trail: [], events: [] });
}

export function connect(source: Source) {
  generation += 1;
  clearTimeout(watchdog); clearTimeout(reconnectTimer); clearTimeout(replayTimer);
  if (ws) { ws.onclose = null; ws.onmessage = null; ws.close(); ws = null; }
  eventLog = []; telemLog = []; fileStartedAt = ''; lastRecording = null;
  store().set({ source, link: 'down', detail: '', fw: '', telem: null, map: null, trail: [], events: [], run: NO_RUN });
  if (source.kind === 'live') openSocket(sameOrigin(source.url), generation, true);
  else void playFile(source, generation);
}

// Through the tunnel (tools/tunnel.sh) or on a phone the page and the robot share one origin:
// Vite proxies /ws, /status, /cmd, /map, /photo and /runs/latest to Scout (vite.config.ts). So
// the default address, which means "the robot next to this laptop", becomes the page's own host
// whenever the page is not on localhost; the /status URL follows, as fetchStatus derives it from
// the socket URL. A typed address is left alone.
function sameOrigin(url: string) {
  const h = location.hostname;
  if (url !== 'ws://localhost:8080/ws' || h === 'localhost' || h === '127.0.0.1') return url;
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
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

export function startRecording() { rec = []; lastRecording = null; recStartedAt = new Date().toISOString(); recLastMap = -Infinity; store().set({ recording: true }); }

// Closes the recording and keeps it as NDJSON (PROTOCOL.md section 7) for the Download menu, until the
// next run starts. Nothing is downloaded here: that is the person's choice, made from the menu.
export function stopRecording(space: string): string | null {
  const frames = rec; rec = null;
  store().set({ recording: false });
  if (!frames?.length) return null;
  const { fw, rules } = store();
  const header: RunHeader = {
    type: 'run', space, fw: fw || 'unknown', started_t: frames[0].t, started_at: recStartedAt,
    config: { width_limit_mm: widthRule(rules)?.limit ?? 860 },
  };
  const text = [header, ...frames].map((x) => JSON.stringify(x)).join('\n') + '\n';
  lastRecording = { space, text };
  return text;
}

// --- live ---

function httpOrigin(wsUrl: string) {
  const u = new URL(wsUrl);
  u.protocol = u.protocol === 'wss:' ? 'https:' : 'http:';
  u.pathname = ''; u.search = '';
  return u.toString().replace(/\/$/, '');
}

// fresh: this is a new connect(), not the reconnect loop after a dropped link.
function openSocket(url: string, gen: number, fresh = false) {
  let s: WebSocket;
  try { s = new WebSocket(url); } catch { store().set({ detail: 'bad URL' }); return; }
  ws = s;
  store().set({ detail: 'connecting' });
  s.onopen = () => {
    if (gen !== generation) return;
    store().set({ detail: '' });
    sendConfig();
    void fetchStatus(url, gen, fresh);
  };
  s.onmessage = (e) => { if (gen !== generation) return; try { handleFrame(JSON.parse(e.data)); } catch { /* not a frame, ignore */ } };
  s.onclose = () => {
    if (gen !== generation) return;
    ws = null; linkDown(); store().set({ detail: 'reconnecting' });
    reconnectTimer = window.setTimeout(() => { if (gen === generation) openSocket(url, gen); }, RECONNECT_MS);
  };
}

// fresh: a page load or a source switch, which starts the map over, but only when Scout is not
// mid-run. On the robot `map clear` also drops the room frame, and re-locking it from wherever
// Scout is parked would leave the rest of the run in a frame the map so far was not drawn in.
async function fetchStatus(wsUrl: string, gen: number, fresh = false) {
  try {
    const st: Status = await (await fetch(`${httpOrigin(wsUrl)}/status`)).json();
    if (gen !== generation) return;
    const d = st.devices ?? { esp32: false, lidar: false, camera: false };
    // `esp32` is protocol v2's old name for `motor` and Scout sends both; report it once.
    // `camera` is always false: Scout has no camera, so its absence is not news.
    const missing = Object.entries(d)
      .filter(([k, ok]) => !ok && k !== 'camera' && !(k === 'esp32' && 'motor' in d))
      .map(([k]) => k);
    // The firmware and the address are not news; a missing device and a protocol mismatch are.
    // RUNBOOK section 1 checks `devices` with curl, which is where the full picture belongs.
    store().set({
      fw: st.fw ?? '',
      detail: (missing.length ? `no ${missing.join('/')}` : '')
        + (st.proto !== PROTO ? `${missing.length ? '  ' : ''}proto ${st.proto}, expected ${PROTO}` : ''),
    });
    const active = !!st.run?.active;
    const run = store().run;
    if (active !== run.active) store().set({ run: { ...run, active, space: st.run?.space ?? '' } });
    if (fresh && !active) send({ cmd: 'map', action: 'clear' });
  } catch { /* status is a nicety, telemetry is the truth. No status, no clear: the map may be mid-run */ }
}

function linkDown() { store().set({ link: 'down' }); }

function handleFrame(f: Frame) {
  if (f.type === 'telem') {
    const s = store();
    // the first pose after run_start is the room frame this run is measured in
    const run = f.pose && s.run.active && !s.run.locked ? { ...s.run, locked: true } : s.run;
    s.set({ telem: f, link: 'up', run });
    if (f.pose) s.addPose(f.x_mm, f.y_mm);
    clearTimeout(watchdog);
    watchdog = window.setTimeout(linkDown, LINK_TIMEOUT_MS);
    rec?.push(f);
    if (telemLog.length < LOG_MAX) telemLog.push(telemRow(f));
  } else if (f.type === 'map') {
    store().set({ map: f });
    if (rec && f.t - recLastMap >= REC_MAP_MS) { rec.push(f); recLastMap = f.t; }
  } else if (f.type === 'event') {
    if (f.kind === 'run_start') {   // a run starts with a cleared map and a fresh room frame (PROTOCOL.md section 5)
      clearBoard();
      const startedAt = store().source.kind === 'replay' ? fileStartedAt : new Date().toISOString();
      store().set({ run: { active: true, space: f.space, startT: f.t, startedAt, locked: false } });
    } else if (f.kind === 'run_stop') {
      store().set({ run: { ...store().run, active: false } });
    }
    store().addEvent(f);
    eventLog.push(f);
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
  // the header is what the file knows about itself: a report made from it says so (report.ts)
  fileStartedAt = header?.started_at ?? '';
  store().set({ detail: '', fw: header?.fw ?? '' });   // the source picker already names the file

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
