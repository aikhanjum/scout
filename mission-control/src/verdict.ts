import type { ScoutEvent } from './protocol';

// Deterministic verdict lines, spoken by the browser and shown in the feed. No AI.
// Width is the only thing Scout judges. A ramp or an obstacle is named, never judged,
// because nothing on Scout measures slope (PROTOCOL.md, top).
export function verdict(e: ScoutEvent): string {
  const cm = (mm: number) => Math.round(mm / 10);
  const value = e.value ?? 0, limit = e.limit ?? 0;
  const where = e.between === 'wall-obstacle' ? 'between a wall and an obstacle' : 'between the walls';
  switch (e.kind) {
    case 'width_fail': return `Too narrow ${where}. ${cm(value)} centimeters. A wheelchair needs ${cm(limit)}.`;
    case 'width_pass': return `Clear ${where}. ${cm(value)} centimeters.`;
    case 'ramp': return 'Ramp here.';
    case 'obstacle': return `Obstacle: ${e.label || 'unknown'}.`;
    case 'mark': return `Marked: ${e.label ?? ''}.`;
    case 'run_start': return `Run started${e.space ? `: ${e.space}` : ''}.`;
    case 'run_stop': return 'Run stopped.';
    default: return e.kind;
  }
}

// What the feed shows under the kind. Obstacles carry how sure the camera was.
export function detail(e: ScoutEvent): string {
  if (e.kind === 'obstacle' || e.kind === 'ramp') {
    const c = e.confidence ? ` ${Math.round(e.confidence * 100)}%` : '';
    return `${e.label || 'unknown'}${c}`;
  }
  if (e.kind === 'width_pass' || e.kind === 'width_fail') return `${e.value} mm / limit ${e.limit} mm · ${e.between ?? ''}`;
  return verdict(e);
}

// Obstacles are frequent, so they are not spoken; ramps and width verdicts are.
export const SPOKEN = new Set(['width_fail', 'width_pass', 'ramp', 'mark']);

export function speak(text: string) {
  if (!('speechSynthesis' in window)) return;
  speechSynthesis.cancel(); // a new verdict replaces one still playing
  speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
