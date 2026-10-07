import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Builds into the Python package so `benchtrace serve` ships the UI.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../src/benchtrace/static',
    emptyOutDir: true,
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8321', '/v1': 'http://127.0.0.1:8321' },
  },
})
