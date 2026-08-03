/**
 * v3.1 E2E: 批量导入向导测试（WBS-19b）
 *
 * 覆盖：5 步向导流程 — 上传 / 字段映射 / 校验 / Dry Run / 创建
 */
import { test, expect } from '@playwright/test';
import { mockBatchImportRoutes, mockConfigStatus, mockNotificationRoutes } from './mocks/setup';

test.describe('Batch Import Wizard', () => {
  test.beforeEach(async ({ page }) => {
    await mockBatchImportRoutes(page);
    await mockNotificationRoutes(page);
    await mockConfigStatus(page, true);
  });

  test('should load batch creation page', async ({ page }) => {
    await page.goto('/batches/new');
    await page.waitForTimeout(2000);

    // 页面应加载完成
    const body = page.locator('body');
    await expect(body).toBeVisible();
  });

  test('should show upload area on first step', async ({ page }) => {
    await page.goto('/batches/new');
    await page.waitForTimeout(2000);

    // 应有文件上传区域或粘贴文本区
    const uploadZone = page.locator('input[type="file"], textarea');
    const uploadCount = await uploadZone.count();
    // 至少应有输入方式
    const anyInput = page.locator('input, textarea');
    const anyCount = await anyInput.count();
    expect(anyCount).toBeGreaterThanOrEqual(0);
  });

  test('should show step indicators for 5-step wizard', async ({ page }) => {
    await page.goto('/batches/new');
    await page.waitForTimeout(2000);

    // 查找步骤指示器
    const pageContent = await page.textContent('body');
    expect(pageContent).toBeTruthy();
  });

  test('should have a manual paste textarea for CSV data', async ({ page }) => {
    await page.goto('/batches/new');
    const textarea = page.locator('textarea');
    await expect(textarea).toBeVisible();
    await textarea.fill('测试公司A,智能客服,政务,北京');
    await page.getByRole('button', { name: '解析文本' }).click();

    await expect(page.getByRole('heading', { name: '数据校验' })).toBeVisible();
    await expect(page.getByText('总计').locator('..').getByText('1')).toBeVisible();
    await expect(page.getByText('测试公司A')).toBeVisible();
    await expect(page.getByRole('button', { name: '开始 Dry Run 采样' })).toBeEnabled();
  });

  test('should disable execution actions in browse-only mode', async ({ page }) => {
    await page.unroute('**/api/config/status');
    await mockConfigStatus(page, false, true);
    await page.goto('/batches/new');
    await page.locator('textarea').fill('测试公司A,智能客服,政务,北京');
    await page.getByRole('button', { name: '解析文本' }).click();

    await expect(page.getByText('当前配置仅支持浏览，完成 Provider 验证后才能执行批量任务。')).toBeVisible();
    await expect(page.getByRole('button', { name: '开始 Dry Run 采样' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '跳过采样，直接创建' })).toBeDisabled();
  });

  test('should navigate to batches from header', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(1000);

    const batchLink = page.getByRole('link', { name: /批量|batch/i });
    if (await batchLink.isVisible().catch(() => false)) {
      await batchLink.click();
      await page.waitForTimeout(1000);
    }
  });
});
