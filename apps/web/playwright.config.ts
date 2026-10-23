import { defineConfig, devices } from '@playwright/test';

const PORT = process.env.PORT || '3030';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `CI=true NEXT_PUBLIC_API_ORIGIN=${process.env.NEXT_PUBLIC_API_ORIGIN || 'http://127.0.0.1:8000'} npm run dev -- -p ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: false,
    timeout: 120000,
  },
});
