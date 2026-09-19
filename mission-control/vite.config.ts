import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// data/ at the repo root is served at / (rules.json, runs/), so the fake Scout and the
// dashboard's replay player read the same files.
export default defineConfig({
  plugins: [react()],
  publicDir: '../data',
  server: { port: 5173, strictPort: true },
});
