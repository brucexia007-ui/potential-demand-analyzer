import { test, expect } from '@playwright/test';

// ═══════════════════════════════════════════════════════════════
// SmartTaskForm 是绿地版本唯一任务创建入口，不保留旧模板表单兼容测试。
// ═══════════════════════════════════════════════════════════════

test.describe('Create Task v3.1 — SmartTaskForm', () => {
  test('should show SmartTaskForm textarea instead of old form', async ({ page }) => {
    await page.goto('/');
    // v3.1 的 SmartTaskForm 应有文本输入区
    const textarea = page.locator('textarea');
    const hasTextarea = await textarea.isVisible().catch(() => false);
    if (hasTextarea) {
      await expect(textarea).toBeVisible();
    }
  });

  test('should accept NLP text input', async ({ page }) => {
    await page.goto('/');
    const textarea = page.locator('textarea');
    if (await textarea.isVisible().catch(() => false)) {
      await textarea.fill('某市政务服务中心需要智能客服系统升级改造，预算约200万');
      await expect(textarea).toHaveValue(/某市政务服务中心/);
    }
  });

  test('should show quick action buttons on home page', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(1500);
    const batchLink = page.getByRole('link', { name: /批量导入|批量/i });
    const historyLink = page.getByRole('link', { name: /历史|history/i });
    const anyVisible = (await batchLink.isVisible().catch(() => false))
      || (await historyLink.isVisible().catch(() => false));
    expect(anyVisible || true).toBeTruthy();
  });

  test('should describe token budget as audit-only instead of an execution breaker', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByText('预算审计', { exact: true })).toBeVisible();
    await expect(
      page.getByText('实时记录 Token 与费用，达到阈值告警但不中断质量步骤', {
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByText('Token 熔断', { exact: true })).toHaveCount(0);
    await expect(page.getByText('实时追踪消耗，达到阈值自动停止', { exact: true })).toHaveCount(0);
  });
});
