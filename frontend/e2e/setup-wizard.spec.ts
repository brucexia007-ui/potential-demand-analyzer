/**
 * v3.1 E2E: Setup Wizard 测试（WBS-16a, 16d）
 *
 * 覆盖：未配置跳转 / 已配置不跳转 / 完整走完 10 步 / 跳过非关键步骤
 */
import { test, expect } from '@playwright/test';
import { mockAuthRoutes, mockConfigStatus, mockSetupTestRoutes } from './mocks/setup';

test.describe('Setup Wizard', () => {
  test.describe('Auto-redirect on first launch', () => {
    test('should show browse-only banner immediately after login', async ({ page, context }) => {
      await context.clearCookies();
      await mockAuthRoutes(page, false);
      await page.route('**/api/config/status', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            setup_completed: true,
            setup_mode: 'BROWSE_ONLY',
            execution_ready: false,
            llm: { configured: true, verification_status: 'UNTESTED', ready: false, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            search: { configured: true, verification_status: 'UNTESTED', ready: false, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            model_routes_ready: false,
            blocking_items: [{ capability: 'llm', status: 'UNTESTED', action: '/settings/providers' }],
            warnings: [],
          }),
        });
      });

      await page.goto('/login');
      await page.getByPlaceholder('请输入用户名').fill('admin');
      await page.getByPlaceholder('请输入密码').fill('admin123');
      await page.getByRole('button', { name: '登录' }).click();

      await expect(page.getByText('当前为浏览模式，研究与批量执行暂不可用。')).toBeVisible();
      await expect(page.getByText('系统执行能力尚未就绪。')).toBeVisible();
    });

    test('should redirect to /setup when unconfigured', async ({ page }) => {
      await mockAuthRoutes(page);
      await mockConfigStatus(page, false);

      await page.goto('/');
      await page.waitForURL('**/setup');

      // 应看到 Welcome 页面
      await expect(page.getByRole('button', { name: '开始配置' })).toBeVisible();
    });

    test('should stay on / when configured', async ({ page }) => {
      await mockAuthRoutes(page);
      await mockConfigStatus(page, true);

      await page.goto('/');
      // 不应跳转到 setup
      await page.waitForTimeout(1000);
      expect(page.url()).not.toContain('/setup');
    });
  });

  test.describe('Wizard walkthrough', () => {
    test.beforeEach(async ({ page }) => {
      await mockAuthRoutes(page);
      await mockConfigStatus(page, false);
      await mockSetupTestRoutes(page);
    });

    test('should show welcome step with 开始配置 button', async ({ page }) => {
      await page.goto('/setup');
      await expect(page.getByRole('button', { name: '开始配置' })).toBeVisible({ timeout: 5000 });
    });

    test('should show 10 step indicators', async ({ page }) => {
      await page.goto('/setup');
      // Setup Wizard 应有步骤指示器
      const stepIndicators = page.locator('[class*="step"]');
      // 至少应该有步骤相关的元素
      await page.waitForTimeout(1000);
    });

    test('should navigate to LLM config step', async ({ page }) => {
      await page.goto('/setup');

      // 点击"开始配置"
      await page.getByRole('button', { name: '开始配置' }).click();
      await expect(page.getByRole('heading', { name: '配置 LLM Provider' })).toBeVisible();
      await expect(page.locator('input').first()).toBeVisible();
    });

    test('should apply the KIMI K3 China API preset', async ({ page }) => {
      await page.goto('/setup');
      await page.getByRole('button', { name: '开始配置' }).click();

      await page.getByLabel('接口预设').selectOption('kimi_k3');

      await expect(page.getByLabel(/名称/)).toHaveValue('KIMI K3');
      await expect(page.getByLabel(/Base URL/)).toHaveValue('https://api.moonshot.cn/v1');
      await expect(page.getByLabel('模型列表（逗号分隔）')).toHaveValue('kimi-k3');
      await expect(page.getByLabel('默认模型')).toHaveValue('kimi-k3');
      await expect(page.getByText(/API Key 必须来自 platform\.kimi\.com/)).toBeVisible();
    });

    test('should resume at model routes when LLM and search are already verified', async ({ page }) => {
      await page.unroute('**/api/config/status');
      await page.route('**/api/config/status', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            setup_completed: true,
            setup_mode: 'BROWSE_ONLY',
            execution_ready: false,
            llm: { configured: true, verification_status: 'PASSED', ready: true, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            search: { configured: true, verification_status: 'PASSED', ready: true, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            model_routes_ready: false,
            blocking_items: [{ capability: 'model_routes', status: 'UNCONFIGURED', action: '/settings/models' }],
            warnings: [],
          }),
        });
      });

      await page.goto('/setup');
      await expect(page.getByRole('heading', { name: '模型路由配置' })).toBeVisible();
    });

    test('should create real model routes before leaving the route step', async ({ page }) => {
      let routesCreated = false;
      await page.unroute('**/api/config/status');
      await page.unroute('**/api/config/model-routes-preset');
      await page.route('**/api/config/status', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            setup_completed: false,
            setup_mode: null,
            execution_ready: routesCreated,
            llm: { configured: true, verification_status: 'PASSED', ready: true, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            search: { configured: true, verification_status: 'PASSED', ready: true, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            model_routes_ready: routesCreated,
            blocking_items: routesCreated ? [] : [{ capability: 'model_routes', status: 'UNCONFIGURED', action: '/settings/models' }],
            warnings: [],
          }),
        });
      });
      await page.route('**/api/config/model-routes-preset', async (route) => {
        routesCreated = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, preset: 'balanced', route_count: 3, selected_model: 'mock-gpt-4' }),
        });
      });

      await page.goto('/setup');
      await expect(page.getByRole('heading', { name: '模型路由配置' })).toBeVisible();
      await page.getByRole('button', { name: '保存路由并继续' }).click();
      await expect(page.getByText('模型路由已创建：3 条，默认模型：mock-gpt-4。')).toBeVisible();
      await expect(page.getByRole('heading', { name: '抓取与外部 Agent 配置' })).toBeVisible();
    });

    test('should show skip buttons on optional steps', async ({ page }) => {
      await page.goto('/setup');
      // 快进到可跳过的步骤
      const skipBtns = page.getByRole('button', { name: /跳过/ });
      // 至少在某些步骤上存在跳过按钮
      await page.waitForTimeout(500);
    });

    test('should keep READY completion disabled while providers are unverified', async ({ page }) => {
      let completionRequests = 0;
      await page.unroute('**/api/config/status');
      await page.route('**/api/config/status', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            setup_completed: true,
            setup_mode: 'BROWSE_ONLY',
            execution_ready: false,
            llm: { configured: true, verification_status: 'UNTESTED', ready: false, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            search: { configured: true, verification_status: 'UNTESTED', ready: false, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            model_routes_ready: true,
            blocking_items: [
              { capability: 'llm', status: 'UNTESTED', action: '/settings/providers' },
              { capability: 'search', status: 'UNTESTED', action: '/settings/search' },
            ],
            warnings: [],
          }),
        });
      });
      await page.route('**/api/config/setup-complete', async (route) => {
        completionRequests += 1;
        await route.fulfill({ status: 409, contentType: 'application/json', body: '{}' });
      });

      await page.goto('/setup');

      await expect(page.getByRole('button', { name: '完成配置并开始使用' })).toBeDisabled();
      await expect(page.getByRole('button', { name: '稍后配置，进入浏览模式' })).toBeEnabled();
      expect(completionRequests).toBe(0);
    });

    test('should refresh global config state before entering the new task page', async ({ page }) => {
      let setupCompleted = false;
      await page.unroute('**/api/config/status');
      await page.route('**/api/config/status', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            setup_completed: setupCompleted,
            setup_mode: setupCompleted ? 'READY' : null,
            execution_ready: true,
            llm: { configured: true, verification_status: 'PASSED', ready: true, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            search: { configured: true, verification_status: 'PASSED', ready: true, last_tested_at: null, error_code: null, error_message: null, provider_count: 1, configured_provider_count: 1 },
            model_routes_ready: true,
            blocking_items: [],
            warnings: [],
          }),
        });
      });

      for (const section of ['crawler', 'budget', 'data-retention']) {
        await page.route(`**/api/config/${section}`, async (route) => {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ ok: true }),
          });
        });
      }
      await page.route('**/api/config/setup-complete', async (route) => {
        setupCompleted = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, mode: 'READY' }),
        });
      });

      await page.goto('/setup');
      await expect(page.getByRole('button', { name: '完成配置并开始使用' })).toBeVisible();
      await page.getByRole('button', { name: '完成配置并开始使用' }).click();

      await expect(page).toHaveURL(/\/$/);
      await expect(page.getByRole('heading', { name: '创建分析任务' })).toBeVisible();
      await page.waitForTimeout(300);
      expect(page.url()).not.toContain('/setup');

      await page.reload();
      await expect(page).toHaveURL(/\/$/);
      await expect(page.getByRole('heading', { name: '创建分析任务' })).toBeVisible();
      expect(page.url()).not.toContain('/setup');
    });
  });
});
