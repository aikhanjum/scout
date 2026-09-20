import type { ScoutEvent } from './protocol';

// Deterministic verdict lines, spoken by the browser and shown in the feed. No AI.
// Width is the only thing Scout judges. An obstacle is placed, never named and never judged:
// there is no camera to say what it is, and nothing on Scout measures slope (PROTOCOL.md, top).
export function verdict(e: ScoutEvent): string {
  const cm = (mm: number) => Math.round(mm / 10);
  const value = e.value ?? 0, limit = e.limit ?? 0;
  const where = e.between === 'wall-obstacle' ? 'between a wall and an obstacle' : 'between the walls';
  switch (e.kind) {
    case 'width_fail': return `Too narrow ${where}. ${cm(value)} centimeters. A wheelchair needs ${cm(limit)}.`;
    case 'width_pass': return `Clear ${where}. ${cm(value)} centimeters.`;
    case 'ramp': return 'Ramp here.';
    case 'obstacle': return 'Obstacle.';
    case 'mark': return `Marked: ${e.label ?? ''}.`;
    case 'run_start': return `Run started${e.space ? `: ${e.space}` : ''}.`;
    case 'run_stop': return 'Run stopped.';
    default: return e.kind;
  }
}

// What the feed shows under the kind. An obstacle is only ever a place: Scout has no camera, so
// its label is always "unknown" and saying so on every row would be noise. A label is shown only
// when a file carries a real one (an older recording).
export function detail(e: ScoutEvent): string {
  if (e.kind === 'obstacle' || e.kind === 'ramp') {
    const at = typeof e.x_mm === 'number' && typeof e.y_mm === 'number'
      ? `at ${(e.x_mm / 1000).toFixed(2)}, ${(e.y_mm / 1000).toFixed(2)} m` : 'no position';
    return e.label && e.label !== 'unknown' ? `${e.label}, ${at}` : at;
  }
  if (e.kind === 'width_pass' || e.kind === 'width_fail') return `${e.value} mm / limit ${e.limit} mm · ${e.between ?? ''}`;
  return verdict(e);
}

// Obstacles are frequent, so they are not spoken; width verdicts and marks are.
export const SPOKEN = new Set(['width_fail', 'width_pass', 'mark']);

export function speak(text: string) {
  if (!('speechSynthesis' in window)) return;
  speechSynthesis.cancel(); // a new verdict replaces one still playing
  speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
