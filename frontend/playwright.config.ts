import { defineConfig } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3001';
const port = new URL(baseURL).port || '3001';

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  retries: 1,
  use: {
    baseURL,
    headless: true,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    storageState: './e2e/.auth/storageState.json',
  },
  globalSetup: process.env.PLAYWRIGHT_SKIP_GLOBAL_SETUP === '1'
    ? undefined
    : './e2e/global-setup.ts',
  webServer: {
    command: `npm run dev -- -p ${port}`,
    url: baseURL,
    reuseExistingServer: !process.env.PLAYWRIGHT_BASE_URL,
    timeout: 30000,
  },
});
