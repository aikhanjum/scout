// Files the dashboard hands out: the run's events and telemetry as CSV tables, the map as a table
// of cells, and the replay file. All built in the browser from what came over /ws; nothing is
// asked of Scout.
import type { MapFrame, ScoutEvent, Telem } from './protocol';

// One telemetry row for the table: the frame without its scan. 360 numbers a frame belong in the
// replay file, not in a spreadsheet.
export type TelemRow = Pick<Telem, 't' | 'mode' | 'measuring' | 'pose' | 'x_mm' | 'y_mm' | 'heading_deg' | 'clearance_mm' | 'v' | 'w' | 'stuck'>;
export const telemRow = (f: Telem): TelemRow => ({
  t: f.t, mode: f.mode, measuring: f.measuring, pose: f.pose, x_mm: f.x_mm, y_mm: f.y_mm, heading_deg: f.heading_deg,
  clearance_mm: f.clearance_mm, v: f.v, w: f.w, stuck: f.stuck,
});

const cell = (v: unknown) => {
  if (v == null) return '';
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
const csv = (header: string[], rows: unknown[][]) => [header, ...rows].map((r) => r.map(cell).join(',')).join('\n') + '\n';

export const eventsCsv = (events: ScoutEvent[]) => csv(
  ['t_ms', 'seq', 'kind', 'label', 'confidence', 'value', 'unit', 'limit', 'between', 'x_mm', 'y_mm', 'space'],
  events.map((e) => [e.t, e.seq, e.kind, e.label, e.confidence, e.value, e.unit, e.limit, e.between, e.x_mm, e.y_mm, e.space]),
);

// Position columns are blank wherever pose was false: the numbers Scout sends then are 0 and mean
// nothing (PROTOCOL.md section 4). So is clearance when there was nothing to measure across.
export const telemetryCsv = (rows: TelemRow[]) => csv(
  ['t_ms', 'mode', 'measuring', 'pose', 'x_mm', 'y_mm', 'heading_deg', 'clearance_mm', 'v', 'w', 'stuck'],
  rows.map((r) => [
    r.t, r.mode, r.measuring, r.pose,
    ...(r.pose ? [r.x_mm, r.y_mm, r.heading_deg] : [null, null, null]),
    r.clearance_mm > 0 ? r.clearance_mm : null, r.v, r.w, r.stuck,
  ]),
);

// One row per cell the map has an opinion on, as the room-frame mm of the cell's centre. Unknown
// cells are left out, so the file is the map and nothing else.
export function mapCsv(map: MapFrame) {
  const rows: unknown[][] = [];
  const [ox, oy] = map.origin;
  for (let i = 0; i < map.cells.length; i++) {
    const c = map.cells[i];
    if (c === '0') continue;
    rows.push([ox + (i % map.w) * map.cell_mm, oy + Math.floor(i / map.w) * map.cell_mm, c === '2' ? 'occupied' : 'free']);
  }
  return csv(['x_mm', 'y_mm', 'state'], rows);
}

// "Room 1" at 2026-09-19 21:04 -> room-1-2026-09-19-21-04
export const fileStem = (space: string) =>
  `${space.trim().replace(/\W+/g, '-').replace(/^-|-$/g, '').toLowerCase() || 'run'}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}`;

// For embedding the map in the report: a data URI, so the file needs nothing beside it.
export const blobToDataUrl = (b: Blob) => new Promise<string | null>((resolve) => {
  const r = new FileReader();
  r.onload = () => resolve(typeof r.result === 'string' ? r.result : null);
  r.onerror = () => resolve(null);
  r.readAsDataURL(b);
});

export function download(name: string, data: Blob | string, type = 'text/plain') {
  const blob = typeof data === 'string' ? new Blob([data], { type }) : data;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
