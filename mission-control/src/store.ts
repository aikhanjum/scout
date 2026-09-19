import { create } from 'zustand';
import type { Rules, ScoutEvent, Spaces, Telem } from './protocol';

export type Source = { kind: 'live'; url: string } | { kind: 'replay'; name: string; text?: string };

interface State {
  source: Source;
  link: 'up' | 'down';   // up only while telemetry is arriving (2 s watchdog in scout.ts)
  detail: string;        // one line next to the link badge: fw and ip, file name, or the error
  telem: Telem | null;   // last frame. Kept when the link drops, shown greyed out.
  events: (ScoutEvent & { id: number })[]; // newest first. id is local: seq restarts when Scout reboots
  scale: number;         // in effect now: TABLE MODE when live, the run header when replaying
  tableMode: boolean;
  voice: boolean;
  recording: boolean;
  rules: Rules | null;
  spaces: Spaces | null;
  set: (patch: Partial<State>) => void;
  addEvent: (e: ScoutEvent) => void;
}

let nextId = 0;

export const useStore = create<State>((set) => ({
  source: { kind: 'live', url: 'ws://localhost:8080/ws' },
  link: 'down', detail: '', telem: null, events: [], scale: 1,
  tableMode: false, voice: true, recording: false, rules: null, spaces: null,
  set: (patch) => set(patch),
  addEvent: (e) => set((s) => ({ events: [{ ...e, id: nextId++ }, ...s.events].slice(0, 100) })),
}));
