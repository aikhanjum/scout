// A run as one page a person can read: the map, the width verdicts, and the obstacles found,
// in a single self contained HTML file. The map goes in as a data URI, so the file can be mailed
// to someone who has never heard of Scout. Chrome prints it to PDF unchanged.
//
// Rule 7: real numbers only, with what they rest on stated. An obstacle is placed, never named or judged.
import type { Rule, ScoutEvent } from './protocol';
import type { TelemRow } from './export';

export interface ReportInput {
  space: string;
  startedAt: string;       // ISO wall clock of run_start, '' when this browser did not start it
  durationMs: number;
  room: { w_mm: number; l_mm: number } | null;
  events: ScoutEvent[];
  telemetry: TelemRow[];
  trail: [number, number][];
  rule: Rule | undefined;
  fw: string;
  mapPng: string | null;   // data URI of the map as drawn, or null when there is no map
}

const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
const m = (mm: number) => (mm / 1000).toFixed(2);
const clock = (ms: number) => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const stamp = (iso: string) => {
  const d = new Date(iso);
  if (!iso || isNaN(+d)) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
// Where an event happened, or a plain note: x_mm and y_mm are absent whenever pose was false
// (PROTOCOL.md section 4), and a position Scout did not have must not be invented here.
const where = (e: ScoutEvent) => (typeof e.x_mm === 'number' && typeof e.y_mm === 'number' ? `${m(e.x_mm)}, ${m(e.y_mm)} m` : 'no position');
const BETWEEN: Record<string, string> = { 'wall-obstacle': 'Wall and obstacle', 'wall-wall': 'Between walls' };

export function buildReport(r: ReportInput): string {
  const widths = r.events.filter((e) => e.kind === 'width_pass' || e.kind === 'width_fail');
  const seen = r.events.filter((e) => e.kind === 'obstacle' || e.kind === 'ramp');
  const marks = r.events.filter((e) => e.kind === 'mark');
  const fails = widths.filter((e) => e.kind === 'width_fail');
  const narrowest = widths.length ? Math.min(...widths.map((e) => e.value ?? Infinity)) : null;
  const limit = r.rule?.limit ?? 860;

  // Path length off the pose track. It is a floor, not a distance travelled: the track holds no
  // points for the stretches where Scout had no pose.
  let driven = 0;
  for (let i = 1; i < r.trail.length; i++) driven += Math.hypot(r.trail[i][0] - r.trail[i - 1][0], r.trail[i][1] - r.trail[i - 1][1]);

  const posed = r.telemetry.length ? r.telemetry.filter((t) => t.pose).length / r.telemetry.length : 0;
  const simulated = /^fake/i.test(r.fw);
  const title = `Scout: ${r.space || 'run'}`;

  const tiles: [string, string, string][] = [   // label, value, class
    ['Room', r.room ? `${m(r.room.w_mm)} × ${m(r.room.l_mm)} m` : 'not locked', r.room ? '' : 'none'],
    ['Path driven', driven ? `${(driven / 1000).toFixed(1)} m` : 'none', driven ? '' : 'none'],
    ['Narrowest gap', narrowest == null ? 'none measured' : `${narrowest} mm`, narrowest == null ? 'none' : narrowest < limit ? 'fail' : 'pass'],
    ['Too narrow', widths.length ? `${fails.length} of ${widths.length}` : 'none measured', fails.length ? 'fail' : widths.length ? 'pass' : 'none'],
  ];

  const row = (cells: string[], cls = '') => `<tr${cls ? ` class="${cls}"` : ''}>${cells.map((c) => `<td>${c}</td>`).join('')}</tr>`;
  const table = (head: string[], rows: string[], empty: string) => rows.length
    ? `<table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`
    : `<p class="empty">${esc(empty)}</p>`;

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<style>
  :root { --ink:#2b2925; --ink-2:#4d4a44; --muted:#6b6760; --rule:#9b9995; --rule-soft:#d7d7d3;
          --paper:#e4e4e1; --sheet:#fff; --sheet-2:#f6f6f4; --fail:#c93535; --pass:#1f6b41; --brand:#e9b74f; --brand-ink:#5a4310;
          --mono: ui-monospace, "IBM Plex Mono", SFMono-Regular, Menlo, monospace; }
  * { box-sizing: border-box; }
  body { margin:0; padding:32px 20px 56px; background:var(--paper); color:var(--ink);
         font:400 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; -webkit-font-smoothing:antialiased; }
  .page { max-width:860px; margin:0 auto; background:var(--sheet); border:1px solid var(--rule); padding:34px 36px 40px; }
  h1 { margin:0; font:600 17px/1 var(--mono); letter-spacing:.14em; text-transform:uppercase; }
  h2 { margin:34px 0 12px; font:600 12px/1 var(--mono); letter-spacing:.08em; text-transform:uppercase; color:var(--ink-2);
       border-bottom:1px solid var(--rule-soft); padding-bottom:8px; }
  .sub { font:500 12px/1.6 var(--mono); letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin-top:9px; }
  .banner { margin:22px 0 0; padding:11px 14px; border:1px solid var(--brand); background:#fbf0d2; color:var(--brand-ink);
            font:600 12px/1.5 var(--mono); letter-spacing:.04em; }
  .tiles { display:grid; grid-template-columns:repeat(4, 1fr); gap:1px; background:var(--rule-soft);
           border:1px solid var(--rule-soft); margin:24px 0 0; }
  .tile { background:var(--sheet-2); padding:13px 14px 15px; }
  .tile .k { font:500 10px/1 var(--mono); letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
  .tile .v { font:600 20px/1.15 var(--mono); margin-top:9px; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .tile.fail .v { color:var(--fail); } .tile.pass .v { color:var(--pass); }
  .tile.none .v { font-size:14px; font-weight:500; color:var(--muted); padding:3px 0; }
  figure { margin:0; }
  figure img { display:block; width:100%; border:1px solid var(--rule); background:var(--sheet); }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th { text-align:left; font:500 10px/1 var(--mono); letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
       padding:0 10px 8px 0; border-bottom:1px solid var(--rule-soft); }
  td { padding:9px 10px 9px 0; border-bottom:1px solid var(--rule-soft); font-variant-numeric:tabular-nums; vertical-align:top; }
  tr:last-child td { border-bottom:0; }
  td:first-child { font:600 11px/1.5 var(--mono); letter-spacing:.06em; text-transform:uppercase; white-space:nowrap; }
  tr.fail td:first-child { color:var(--fail); } tr.pass td:first-child { color:var(--pass); }
  .empty, .note { color:var(--muted); font-size:13px; }
  .note { margin:12px 0 0; max-width:66ch; }
  footer { margin-top:34px; padding-top:16px; border-top:1px solid var(--rule-soft); color:var(--muted); font-size:12px; line-height:1.7; }
  @media print {
    body { background:#fff; padding:0; }
    .page { border:0; max-width:none; padding:0; }
    h2, figure, table { break-inside:avoid; }
  }
</style></head>
<body><main class="page">
  <h1>Scout &middot; Accessibility report</h1>
  <p class="sub">${esc(r.space || 'Unnamed run')}${stamp(r.startedAt) ? ` &middot; ${esc(stamp(r.startedAt))}` : ''}${r.durationMs > 0 ? ` &middot; ${clock(r.durationMs)}` : ''}</p>
  ${simulated ? '<p class="banner">Simulated run. These numbers come from a simulated room, not from a measurement of a real space.</p>' : ''}

  <div class="tiles">${tiles.map(([k, v, cls]) => `<div class="tile ${cls}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('')}</div>

  <h2>The map</h2>
  ${r.mapPng
      ? `<figure><img src="${r.mapPng}" alt="The occupancy map of ${esc(r.space || 'the room')}, as Scout drew it."></figure>`
      : '<p class="empty">No map was drawn. Scout maps only while it has a pose.</p>'}

  <h2>Width verdicts</h2>
  ${table(['Result', 'Width', 'Between', 'Where'],
    widths.map((e) => row([
      e.kind === 'width_fail' ? 'Too narrow' : 'Clear',
      `${esc(e.value)} mm`,
      esc(BETWEEN[e.between ?? ''] ?? e.between ?? ''),
      esc(where(e)),
    ], e.kind === 'width_fail' ? 'fail' : 'pass')),
    'No gap narrow enough to measure across. In open space there is nothing to report.')}
  <p class="note">${r.rule?.text ? `${esc(r.rule.text)}${r.rule.source ? ` ${esc(r.rule.source)}.` : ''}` : `A gap must be at least ${limit} mm of clear width to be an accessible route.`}</p>

  <h2>Obstacles</h2>
  ${table(['Obstacle', 'Where', 'At'],
    seen.map((e, i) => row([
      `#${i + 1}`,
      esc(where(e)),
      `${(e.t / 1000).toFixed(1)} s`,
    ])),
    'No obstacle came within reach of the path. Scout reports what it has been close to, not everything it can see.')}
  <p class="note">These are observations, not verdicts. Scout has no camera, so it places an obstacle and never says what it is; its footprint is on the map. Nothing on Scout measures slope, so a ramp is not told apart from a wall.</p>

  ${marks.length ? `<h2>Marks</h2>${table(['Mark', 'Where', 'At'], marks.map((e) => row([esc(e.label || 'mark'), esc(where(e)), `${(e.t / 1000).toFixed(1)} s`])), '')}` : ''}

  <footer>
    Width is measured by the lidar; it is the only thing Scout judges. Positions are read off the room's fitted rectangle, so they exist only inside one rectangular room: a position is blank wherever Scout had none.
    Pose held for ${Math.round(posed * 100)}% of ${r.telemetry.length} telemetry frames. Path driven is the length of the pose track, so it is a floor, not a total.<br>
    Written by Scout Mission Control from the run's own frames${r.fw ? `, firmware ${esc(r.fw)}` : ''}. The matching .ndjson replays the whole run frame by frame.
  </footer>
</main></body></html>
`;
}
