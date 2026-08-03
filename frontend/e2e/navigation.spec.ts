import { test, expect } from '@playwright/test';
import { mockTasksListRoutes } from './mocks/setup';

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await mockTasksListRoutes(page);
  });

  test('should show header with logo and links', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('潜在需求分析系统')).toBeVisible();
    await expect(page.getByRole('link', { name: '新建任务' })).toBeVisible();
    await expect(page.getByRole('link', { name: '历史记录' })).toBeVisible();
  });

  test('should navigate from home to history via header link', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: '历史记录' }).click();
    await expect(page).toHaveURL('/history');
    await expect(page.getByRole('heading', { name: '历史任务' })).toBeVisible();
  });

  test('should navigate from history to home via header link', async ({ page }) => {
    await page.goto('/history');
    await page.getByRole('link', { name: '新建任务' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: '创建分析任务' })).toBeVisible();
  });

  test('should show username and logout button in header', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('banner').getByText('admin', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '登出' })).toBeVisible();
  });

  test('should logout and redirect to login', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: '登出' }).click();
    await expect(page).toHaveURL(/\/login/);
  });

  test('should navigate back from task detail via browser back', async ({ page }) => {
    await page.goto('/history');
    const taskCard = page.getByText('测试企业A');
    await expect(taskCard).toBeVisible({ timeout: 10000 });
    await taskCard.click();
    await expect(page).toHaveURL(/\/tasks\/[a-f0-9-]{36}/);

    await page.goBack();
    await expect(page).toHaveURL('/history');
  });
});
