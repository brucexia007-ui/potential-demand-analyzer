import { test, expect } from '@playwright/test';

const E2E_ORIGIN = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3001';

test.describe('Auth — Login', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('should show login form', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('heading', { name: '登录' })).toBeVisible();
    await expect(page.getByPlaceholder('请输入用户名')).toBeVisible();
    await expect(page.getByPlaceholder('请输入密码')).toBeVisible();
    await expect(page.getByRole('button', { name: '登录' })).toBeVisible();
  });

  test('should not expose a fixed default credential hint', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByText('演示账号：')).toHaveCount(0);
    await expect(page.getByText('admin / admin123')).toHaveCount(0);
  });

  test('should navigate to register page', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('link', { name: '注册账号' }).click();
    await expect(page).toHaveURL('/register');
    await expect(page.getByRole('heading', { name: '注册功能暂未开放' })).toBeVisible();
  });

  test('should show error on empty form submission', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('button', { name: '登录' }).click();
    // Should not crash — either HTML5 validation or error message
    await page.waitForTimeout(1000);
    const heading = page.getByRole('heading', { name: '登录' });
    await expect(heading).toBeVisible();
  });
});

test.describe('Auth — Register', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('should show register placeholder with back link', async ({ page }) => {
    await page.goto('/register');
    await expect(page.getByRole('heading', { name: '注册功能暂未开放' })).toBeVisible();
    await page.getByRole('button', { name: '返回登录' }).click();
    await expect(page).toHaveURL('/login');
  });
});

test.describe('Auth — Protected Routes', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('should redirect unauthenticated user from / to /login', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/login/);
  });

  test('should redirect unauthenticated user from /history to /login', async ({ page }) => {
    await page.goto('/history');
    await expect(page).toHaveURL(/\/login\?redirect=%2Fhistory/);
  });

  test('should reject an external redirect after login', async ({ page, context }) => {
    let loggedIn = false;
    await page.route('**/api/auth/me', async (route) => route.fulfill(
      loggedIn
        ? {
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ id: 'test-user', username: 'admin', is_active: true }),
          }
        : { status: 401 },
    ));
    await page.route('**/api/auth/refresh', async (route) => route.fulfill({ status: 401 }));
    await page.route('**/api/auth/login', async (route) => {
      loggedIn = true;
      await context.addCookies([{
        name: 'kanyikan_access',
        value: 'mock-access-cookie',
        url: E2E_ORIGIN,
        httpOnly: true,
        sameSite: 'Lax',
      }]);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          username: 'admin',
          access_expires_in_seconds: 1800,
          session_expires_in_seconds: 604800,
        }),
      });
    });

    await page.goto('/login?redirect=%2F%2Fevil.example');
    await page.getByPlaceholder('请输入用户名').fill('admin');
    await page.getByPlaceholder('请输入密码').fill('admin123');
    await page.getByRole('button', { name: '登录' }).click();
    await expect(page).toHaveURL(`${E2E_ORIGIN}/`);
  });
});

test.describe('Auth — Session Recovery', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('should use one refresh request for concurrent 401 responses', async ({ page, context }) => {
    await context.addCookies([{
      name: 'kanyikan_refresh',
      value: 'test-refresh-cookie',
      url: E2E_ORIGIN,
      httpOnly: true,
      sameSite: 'Lax',
    }]);
    let refreshed = false;
    let refreshCount = 0;
    await page.route('**/api/auth/refresh', async (route) => {
      refreshCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 200));
      refreshed = true;
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });
    await page.route('**/api/auth/me', async (route) => route.fulfill(
      refreshed
        ? { status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'u1', username: 'admin', is_active: true }) }
        : { status: 401 },
    ));
    await page.route('**/api/tasks**', async (route) => route.fulfill(
      refreshed
        ? { status: 200, contentType: 'application/json', body: JSON.stringify({ total: 0, page: 1, page_size: 20, tasks: [] }) }
        : { status: 401 },
    ));

    await page.goto('/history');
    await expect(page.getByRole('heading', { name: '历史任务' })).toBeVisible();
    await expect.poll(() => refreshCount).toBe(1);
  });

  test('should keep the current page when auth service returns 5xx', async ({ page, context }) => {
    await context.addCookies([{
      name: 'kanyikan_refresh',
      value: 'test-refresh-cookie',
      url: E2E_ORIGIN,
      httpOnly: true,
      sameSite: 'Lax',
    }]);
    await page.route('**/api/auth/me', async (route) => route.fulfill({ status: 503 }));
    await page.route('**/api/tasks**', async (route) => route.fulfill({ status: 503 }));

    await page.goto('/history');
    await expect(page).toHaveURL(/\/history$/);
  });
});
