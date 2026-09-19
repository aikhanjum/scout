// Types for docs/PROTOCOL.md, version 1. Keys match the doc exactly.

export type Mode = 'idle' | 'teleop';

export interface Telem {
  type: 'telem'; t: number; mode: Mode; measuring: boolean;
  imu?: boolean; lidar?: boolean; // v1.1: false means that reading is missing, not zero. Absent (fake, old files) means present.
  pitch_deg: number; roll_deg: number; yaw_deg: number;
  sweep: { a: number; mm: number }[]; width_mm: number;
  bump: [number, number]; stuck: boolean; v: number; w: number;
}

export type EventKind =
  | 'slope_pass' | 'slope_fail' | 'width_pass' | 'width_fail'
  | 'mark' | 'run_start' | 'run_stop' | 'tilt_cutoff' | 'bump' | 'stuck';

export interface ScoutEvent {
  type: 'event'; t: number; seq: number; kind: EventKind; space: string;
  value?: number; unit?: string; limit?: number; scale?: number; label?: string;
}

export type Frame = Telem | ScoutEvent;

export interface Config { slope_limit_deg: number; width_limit_mm: number; scale: number; width_offset_mm: number }

export interface RunHeader { type: 'run'; space: string; fw: string; started_t: number; config?: Partial<Config> }

export type Command =
  | { cmd: 'drive'; v: number; w: number }
  | { cmd: 'stop' }
  | { cmd: 'mode'; mode: Mode }
  | { cmd: 'run'; action: 'start'; space: string }
  | { cmd: 'run'; action: 'stop' }
  | { cmd: 'mark'; label: string }
  | { cmd: 'zero' }
  | { cmd: 'beep' }
  | ({ cmd: 'config' } & Partial<Config>);

export interface Status {
  proto: number; fw: string; mode: Mode; measuring: boolean;
  run: { active: boolean; space: string }; config: Config;
  uptime_ms: number; heap: number; ip: string;
}

// data/rules.json and data/spaces.json (PROTOCOL.md section 7)

export interface Rule { id: string; kind: 'slope' | 'width'; limit: number; unit: string; cmp: 'max' | 'min'; text: string; source: string; fix: string }
export interface Rules { table_scale: number; rules: Rule[] }

export interface Checkpoint {
  id: string; rule: string | null; value?: number; unit?: string;
  source: 'scout' | 'tape' | 'level' | 'mark'; note?: string; position?: [number, number, number];
}
export interface Space { id: string; name: string; model?: string; checkpoints: Checkpoint[] }
export interface Spaces { spaces: Space[] }

export const passes = (rule: Rule, value: number) => (rule.cmp === 'max' ? value <= rule.limit : value >= rule.limit);

// slope_* events use the slope rule, width_* the width rule
export function ruleFor(rules: Rules | null, kind: string): Rule | undefined {
  const k = kind.startsWith('slope') ? 'slope' : kind.startsWith('width') ? 'width' : null;
  return k ? rules?.rules.find((r) => r.kind === k) : undefined;
}
