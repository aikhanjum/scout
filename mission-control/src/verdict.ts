import type { ScoutEvent } from './protocol';

// Deterministic verdict lines, spoken by the browser and shown in the feed. No AI.
// Scaled course numbers are spoken at full scale and say so.
export function verdict(e: ScoutEvent): string {
  const scale = e.scale ?? 1;
  const value = e.value ?? 0, limit = e.limit ?? 0;
  const cm = (mm: number) => Math.round(mm / scale / 10);
  const fs = scale !== 1 ? ' at full scale' : '';
  switch (e.kind) {
    case 'slope_fail': return `Ramp too steep. ${value.toFixed(1)} degrees. The limit is ${limit.toFixed(1)}.`;
    case 'slope_pass': return `Ramp OK. ${value.toFixed(1)} degrees.`;
    case 'width_fail': return `Doorway too narrow. ${cm(value)} centimeters${fs}. A wheelchair needs ${cm(limit)}.`;
    case 'width_pass': return `Doorway OK. ${cm(value)} centimeters${fs}.`;
    case 'mark': return `Marked: ${e.label ?? ''}.`;
    case 'tilt_cutoff': return 'Tilt cutoff. Motors stopped.';
    case 'run_start': return `Run started${e.space ? `: ${e.space}` : ''}.`;
    case 'run_stop': return 'Run stopped.';
    default: return e.kind;
  }
}

export const SPOKEN = new Set(['slope_fail', 'slope_pass', 'width_fail', 'width_pass', 'mark', 'tilt_cutoff']);

export function speak(text: string) {
  if (!('speechSynthesis' in window)) return;
  speechSynthesis.cancel(); // a new verdict replaces one still playing
  speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
