// Types for docs/PROTOCOL.md, version 2. Keys match the doc exactly.

export const PROTO = 2;

export type Mode = 'idle' | 'teleop' | 'wall_follow';

export interface Telem {
  type: 'telem'; t: number; mode: Mode; measuring: boolean;
  lidar: boolean;
  scan: number[];              // exactly 360 entries, or [] when the lidar is absent.
                               // index i = range in mm at bearing i degrees CCW of straight ahead.
  gaps: ScanGap[];             // openings found in that scan, [] without a lidar
  pose: boolean;               // false: the room frame is not locked. x/y/heading are 0 and mean nothing.
  x_mm: number; y_mm: number; heading_deg: number;
  room: { w_mm: number; l_mm: number } | null;
  clearance_mm: number;        // narrowest gap across the path right now, 0 when unknown
  bump: [number, number]; stuck: boolean; v: number; w: number;
}

export type Evidence = 'see_through' | 'step' | 'unverified';

// An opening found in one rotation. `evidence` is how well supported it is: a lidar cannot tell an
// opening from a surface that does not reflect, so `unverified` must never be shown as a measurement.
export interface ScanGap {
  a0: number; mm0: number; a1: number; mm1: number;
  width_mm: number; span_deg: number; mid_deg: number; evidence: Evidence;
}

export interface MapFrame {
  type: 'map'; t: number; cell_mm: number; w: number; h: number;
  origin: [number, number];    // room-frame mm of the centre of cell (0,0)
  cells: string;               // w*h chars, row-major: '0' unknown, '1' free, '2' occupied
  pose: boolean;
}

export type EventKind =
  | 'obstacle' | 'ramp' | 'width_pass' | 'width_fail'
  | 'mark' | 'run_start' | 'run_stop' | 'bump' | 'stuck';

export interface ScoutEvent {
  type: 'event'; t: number; seq: number; kind: EventKind; space: string;
  label?: string; confidence?: number; photo?: string;   // obstacle, ramp
  x_mm?: number; y_mm?: number;                          // room frame, present only when pose was true
  value?: number; unit?: string; limit?: number; between?: string;   // width_*
}

export type Frame = Telem | ScoutEvent | MapFrame;

export interface Config { width_limit_mm: number; robot_width_mm: number; wall_target_mm: number; cruise: number }

export interface RunHeader { type: 'run'; space: string; fw: string; started_t: number; started_at?: string; config?: Partial<Config> } // started_at: ISO 8601 UTC wall clock of started_t (v2.1, absent in older files)

export type Command =
  | { cmd: 'drive'; v: number; w: number }
  | { cmd: 'stop' }
  | { cmd: 'mode'; mode: Mode }
  | { cmd: 'run'; action: 'start'; space: string }
  | { cmd: 'run'; action: 'stop' }
  | { cmd: 'mark'; label: string }
  | { cmd: 'map'; action: 'clear' }
  | { cmd: 'beep' }
  | ({ cmd: 'config' } & Partial<Config>);

export interface Status {
  proto: number; fw: string; mode: Mode; measuring: boolean;
  run: { active: boolean; space: string };
  devices: { esp32: boolean; lidar: boolean; camera: boolean };
  config: Config; uptime_ms: number; ip: string;
}

// data/rules.json (PROTOCOL.md section 8). There is no slope rule: Scout cannot measure slope.
export interface Rule { id: string; kind: 'width'; limit: number; unit: string; cmp: 'max' | 'min'; text: string; source: string; fix: string }
export interface Rules { rules: Rule[]; labels: string[] }

export const passes = (rule: Rule, value: number) => (rule.cmp === 'max' ? value <= rule.limit : value >= rule.limit);

export const widthRule = (rules: Rules | null) => rules?.rules.find((r) => r.kind === 'width');

export const isPlaced = (e: ScoutEvent) => typeof e.x_mm === 'number' && typeof e.y_mm === 'number';
