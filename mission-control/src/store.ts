import { create } from 'zustand';
import type { MapFrame, Rules, ScoutEvent, Telem } from './protocol';

export type Source = { kind: 'live'; url: string } | { kind: 'replay'; name: string; text?: string };

interface State {
  source: Source;
  link: 'up' | 'down';   // up only while telemetry is arriving (2 s watchdog in scout.ts)
  detail: string;        // one line next to the link badge: fw and ip, file name, or the error
  telem: Telem | null;   // last frame. Kept when the link drops, shown greyed out.
  map: MapFrame | null;  // last map frame. Kept across a link drop so the view never blanks.
  trail: [number, number][]; // where Scout has been this run, room mm, oldest first. Only while pose was true.
  events: (ScoutEvent & { id: number })[]; // newest first. id is local: seq restarts when Scout reboots
  voice: boolean;
  recording: boolean;
  rules: Rules | null;
  baseUrl: string;       // http origin of the live Scout, for /photo/<id>
  set: (patch: Partial<State>) => void;
  addEvent: (e: ScoutEvent) => void;
  addPose: (x: number, y: number) => void;
}

let nextId = 0;

export const useStore = create<State>((set) => ({
  source: { kind: 'live', url: 'ws://localhost:8080/ws' },
  link: 'down', detail: '', telem: null, map: null, trail: [], events: [],
  voice: true, recording: false, rules: null, baseUrl: '',
  set: (patch) => set(patch),
  addEvent: (e) => set((s) => ({ events: [{ ...e, id: nextId++ }, ...s.events].slice(0, 100) })),
  // a point every 25 mm of travel is plenty for a line; the cap is hours of driving
  addPose: (x, y) => set((s) => {
    const last = s.trail[s.trail.length - 1];
    if (last && Math.hypot(last[0] - x, last[1] - y) < 25) return {};
    const point: [number, number] = [x, y];
    return { trail: [...s.trail, point].slice(-6000) };
  }),
}));

// Every placed event, oldest first, one per position: what the map draws.
export const placedEvents = (events: (ScoutEvent & { id: number })[]) =>
  events.filter((e) => typeof e.x_mm === 'number' && typeof e.y_mm === 'number').slice().reverse();
