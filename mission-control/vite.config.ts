import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// data/ at the repo root is served at / (rules.json, runs/), so the fake Scout and the
// dashboard's replay player read the same files.
//
// One origin for everything. A phone on the hotspot or a judge through the cloudflared tunnel
// (tools/tunnel.sh) can only reach this port, so Vite forwards Scout's endpoints and the Tiger
// tailer's from here: /ws (websocket), /status, /cmd, /map and /photo go to Scout (SCOUT_HOST,
// default localhost:8080) and /tiger goes to live_tail.py (TAIL_HOST, default 127.0.0.1:8787).
// scout.ts opens ws://<page host>/ws when the page is not on localhost, so the browser never has
// to reach the robot itself. Only the exact path /runs/latest is proxied (regex key): every
// other /runs/<file> must keep coming from publicDir, or replays break. allowedHosts: the
// tunnel's hostname is random and Vite would otherwise refuse it.
declare const process: { env: Record<string, string | undefined> };   // no @types/node in this tsconfig
const SCOUT = process.env.SCOUT_HOST ?? 'localhost:8080';
const TAIL = process.env.TAIL_HOST ?? '127.0.0.1:8787';
const scout = { target: `http://${SCOUT}`, changeOrigin: true };

export default defineConfig({
  plugins: [react()],
  publicDir: '../data',
  server: {
    port: 5173,
    strictPort: true,
    allowedHosts: true,
    proxy: {
      '/ws': { target: `ws://${SCOUT}`, ws: true, changeOrigin: true },
      '/status': scout,
      '/cmd': scout,
      '/map': scout,
      '/photo': scout,
      '^/runs/latest$': scout,
      '/tiger': { target: `http://${TAIL}`, changeOrigin: true },
    },
  },
});
