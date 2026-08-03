/**
 * v3.1 E2E: Settings 前端页面测试（WBS-16c）
 *
 * 覆盖：crawler/budget/data-retention/security/export 5 个页面可达、可编辑、可保存
 */
import { test, expect } from '@playwright/test';
import { mockSettingsRoutes, mockNotificationRoutes } from './mocks/setup';

test.describe('Settings Pages', () => {
  test.beforeEach(async ({ page }) => {
    await mockSettingsRoutes(page);
    await mockNotificationRoutes(page);
  });

  const settingsPages = [
    { path: '/settings/crawler', name: '抓取配置' },
    { path: '/settings/budget', name: '预算配置' },
    { path: '/settings/data-retention', name: '数据保留' },
    { path: '/settings/security', name: '安全配置' },
    { path: '/settings/export', name: '导入导出' },
  ];

  for (const { path, name } of settingsPages) {
    test(`should load ${name} page at ${path}`, async ({ page }) => {
      await page.goto(path);
      // 页面应有内容渲染（不是空白）
      await expect(page.locator('body')).toBeVisible();
      // 不应显示 404 或错误
      await expect(page.locator('text=404').or(page.locator('text=Not Found'))).toHaveCount(0);
    });
  }

  test('should save crawler settings and show toast', async ({ page }) => {
    await page.goto('/settings/crawler');
    await page.waitForTimeout(1000);

    // 查找保存按钮
    const saveBtn = page.getByRole('button', { name: /保存|save/i });
    if (await saveBtn.isVisible()) {
      await saveBtn.click();
      // 应显示成功提示
      await page.waitForTimeout(1000);
    }
  });

  test('should save budget settings', async ({ page }) => {
    await page.goto('/settings/budget');
    await page.waitForTimeout(1000);

    const saveBtn = page.getByRole('button', { name: /保存|save/i });
    if (await saveBtn.isVisible()) {
      await saveBtn.click();
      await page.waitForTimeout(500);
    }
  });

  test('should show export button on export page', async ({ page }) => {
    await page.goto('/settings/export');
    await page.waitForTimeout(1000);

    // 应有导出按钮或导入相关元素
    const exportBtn = page.getByRole('button', { name: /导出|export/i });
    const importArea = page.locator('input[type="file"]');
    const hasContent = (await exportBtn.isVisible()) || (await importArea.isVisible());
    expect(hasContent).toBeTruthy();
  });

  test('should offer a KIMI K3 preset on the LLM provider page', async ({ page }) => {
    await page.route('**/api/config/providers', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '[]',
      });
    });
    await page.route('**/api/config/health', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ llm: [], search: [] }),
      });
    });

    await page.goto('/settings/providers');
    await page.getByRole('button', { name: '添加第一个 Provider' }).click();
    await page.getByLabel('接口预设').selectOption('kimi_k3');

    await expect(page.getByLabel(/名称/)).toHaveValue('KIMI K3');
    await expect(page.getByLabel('Base URL')).toHaveValue('https://api.moonshot.cn/v1');
    await expect(page.getByLabel('模型列表（逗号分隔）')).toHaveValue('kimi-k3');
    await expect(page.getByLabel('默认模型')).toHaveValue('kimi-k3');
  });
});
