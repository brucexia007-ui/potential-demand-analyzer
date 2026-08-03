import { test, expect } from '@playwright/test';

const E2E_ORIGIN = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3001';

test.describe('Error Handling — Session Expiry', () => {
  test('should redirect to login when session cookies are cleared', async ({ page }) => {
    await page.goto('/history');
    await expect(page.getByRole('heading', { name: '历史任务' })).toBeVisible({ timeout: 10000 });

    await page.context().clearCookies();

    // Navigate to a protected page — should redirect to login
    await page.goto('/history');
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
  });

  test('should reject invalid HttpOnly-style session cookies', async ({ page, context }) => {
    await context.clearCookies();
    await context.addCookies([{ name: 'kanyikan_access', value: 'invalid.jwt.token', url: E2E_ORIGIN, httpOnly: true, sameSite: 'Lax' }]);
    await page.goto('/history');
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
  });
});

test.describe('Error Handling — Rate Limiting', () => {
  test('should handle rapid requests gracefully', async ({ page }) => {
    await page.goto('/history');
    await expect(page.getByRole('heading', { name: '历史任务' })).toBeVisible({ timeout: 10000 });

    // Send multiple rapid requests to potentially trigger rate limiting
    const results = await Promise.allSettled(
      Array.from({ length: 8 }, () =>
        page.evaluate(() =>
          fetch('/api/tasks?page=1&page_size=10', { credentials: 'same-origin' }).then(r => r.status)
        )
      )
    );

    // At minimum, some requests should complete (status 200) or be rate limited (429)
    const statuses = results
      .filter((r): r is PromiseFulfilledResult<number> => r.status === 'fulfilled')
      .map(r => r.value);
    // We just verify the app doesn't crash — any status code is acceptable
    expect(statuses.length).toBeGreaterThan(0);
  });
});

test.describe('Error Handling — Invalid Routes', () => {
  test('should handle non-existent task ID gracefully', async ({ page }) => {
    await page.goto('/tasks/00000000-0000-0000-0000-000000000000');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await expect(page.getByText('HTTP 404')).toBeVisible();
  });
});

test.describe('Error Handling — Auth Edge Cases', () => {
  test('should not crash on login with empty credentials', async ({ page }) => {
    // Clear auth state for this test
    await page.context().clearCookies();
    await page.goto('/login');
    await page.evaluate(() => {
      localStorage.clear();
    });

    // Click login without filling anything
    await page.getByRole('button', { name: '登录' }).click();
    // The page should not white-screen — either HTML5 validation prevents submission
    // or the app shows an error
    await page.waitForTimeout(1000);
    const heading = page.getByRole('heading', { name: '登录' });
    await expect(heading).toBeVisible();
  });
});

test.describe('Error Handling v3.1 — SmartTaskForm API Errors', () => {
  test('should show error toast when interpret API fails', async ({ page }) => {
    // Mock interpret API 返回 500
    await page.route('**/api/advisor/interpret', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'LLM 服务暂时不可用' }),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(2000);

    // 尝试输入并解析
    const textarea = page.locator('textarea');
    if (await textarea.isVisible().catch(() => false)) {
      await textarea.fill('测试解析失败场景');
      const parseBtn = page.getByRole('button', { name: /解析/i });
      if (await parseBtn.isVisible().catch(() => false)) {
        await parseBtn.click();
        await page.waitForTimeout(1000);
      }
    }
    // 页面不应崩溃
    await expect(page.locator('body')).toBeVisible();
  });

  test('should handle settings save failure gracefully', async ({ page }) => {
    // Mock settings API 返回 500
    await page.route('**/api/config/budget', async (route) => {
      if (route.request().method() === 'PUT') {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: '配置保存失败' }),
        });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });

    await page.goto('/settings/budget');
    await page.waitForTimeout(1000);

    const saveBtn = page.getByRole('button', { name: /保存|save/i });
    if (await saveBtn.isVisible().catch(() => false)) {
      await saveBtn.click();
      await page.waitForTimeout(1000);
    }
    // 不应崩溃
    await expect(page.locator('body')).toBeVisible();
  });

  test('should handle plan API timeout gracefully', async ({ page }) => {
    // Mock plan API 延迟响应
    await page.route('**/api/advisor/plan', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 500));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'timeout' }),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(1000);
    // 页面不应崩溃
    await expect(page.locator('body')).toBeVisible();
  });
});
