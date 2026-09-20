// Terminal palette and the one distance colour scale every view shares, so a red slab in
// Scout's eyes and a red dot on the radar mean the same distance.
export const C = {
  bg: '#0b0d10', panel: '#12151a', rule: '#262b33', fg: '#d7dde5', dim: '#8a939e',
  amber: '#e9b74f', red: '#e0443e', green: '#3ddc84', blue: '#4da3ff',
};

// Stops in mm. Between stops the colour is a straight RGB blend; outside them it is clamped.
const STOPS: [number, [number, number, number]][] = [
  [400, [224, 68, 62]],    // red: within reach
  [860, [233, 183, 79]],   // amber: the building-code width
  [2000, [61, 220, 132]],  // green: room to move
  [5000, [77, 163, 255]],  // blue: far
];

export function distanceColour(mm: number): string {
  if (mm <= STOPS[0][0]) return rgb(STOPS[0][1]);
  for (let i = 1; i < STOPS.length; i++) {
    const [d1, c1] = STOPS[i];
    if (mm <= d1) {
      const [d0, c0] = STOPS[i - 1];
      const t = (mm - d0) / (d1 - d0);
      return rgb([0, 1, 2].map((k) => Math.round(c0[k] + (c1[k] - c0[k]) * t)) as [number, number, number]);
    }
  }
  return rgb(STOPS[STOPS.length - 1][1]);
}

const rgb = (c: [number, number, number]) => `rgb(${c[0]},${c[1]},${c[2]})`;
