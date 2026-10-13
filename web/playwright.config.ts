import { defineConfig } from '@playwright/test'

// Expects a running server: `everybench serve` (default http://127.0.0.1:8321).
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  use: { baseURL: process.env.EVERYBENCH_URL ?? 'http://127.0.0.1:8321', viewport: { width: 1360, height: 900 } },
})
