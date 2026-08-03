import { defineConfig } from '@playwright/test';

// ═══════════════════════════════════════════════════════════════
// 真实环境 E2E 专用配置：打 docker 已运行的全套系统（无 mock）。
// 与 e2e/ 下的 mock 测试完全隔离：
//   - 无 globalSetup（避免覆写 setup 模式 / 注册 Mock Provider）
//   - 无 webServer（复用 10443 上已运行的唯一 HTTPS 前端入口）
// 运行：npx.cmd playwright test -c playwright.real.config.ts
// ═══════════════════════════════════════════════════════════════

export default defineConfig({
  testDir: './e2e-real',
  timeout: 0, // 单测试内自行管理长轮询（真实任务执行 5–30 分钟）
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.REAL_BASE_URL || 'https://127.0.0.1:10443',
    ignoreHTTPSErrors: true,
    headless: true,
    screenshot: 'on',
    trace: 'retain-on-failure',
  },
  outputDir: './e2e-real/artifacts/test-results',
});
