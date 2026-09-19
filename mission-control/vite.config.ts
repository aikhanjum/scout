import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// data/ at the repo root is served at / (rules.json, spaces.json, runs/, models/),
// so the fake Scout, the replay player and the 3D viewer all read the same files.
export default defineConfig({
  plugins: [react()],
  publicDir: '../data',
  server: { port: 5173, strictPort: true },
});
