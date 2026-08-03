import { test, expect } from '@playwright/test';
import { mockTasksListRoutes } from './mocks/setup';

test.describe('History Page', () => {
  test.beforeEach(async ({ page }) => {
    await mockTasksListRoutes(page);
  });

  test('should show the history page header', async ({ page }) => {
    await page.goto('/history');
    await expect(page.getByRole('heading', { name: '历史任务' })).toBeVisible();
    await expect(page.getByRole('button', { name: '新建任务' })).toBeVisible();
  });

  test('should show status filter tabs', async ({ page }) => {
    await page.goto('/history');
    await expect(page.getByRole('button', { name: '全部' })).toBeVisible();
    await expect(page.getByRole('button', { name: '执行中' })).toBeVisible();
    await expect(page.getByRole('button', { name: '已完成' })).toBeVisible();
    await expect(page.getByRole('button', { name: '部分完成' })).toBeVisible();
    await expect(page.getByRole('button', { name: '已失败' })).toBeVisible();
    await expect(page.getByRole('button', { name: '已暂停' })).toBeVisible();
    await expect(page.getByRole('button', { name: '已取消' })).toBeVisible();
    await expect(page.getByRole('button', { name: '等待中' })).toBeVisible();
  });

  test('should show search input', async ({ page }) => {
    await page.goto('/history');
    const searchInput = page.getByPlaceholder('搜索公司名或需求方向...');
    await expect(searchInput).toBeVisible();
  });

  test('should show "新建任务" button navigates to home', async ({ page }) => {
    await page.goto('/history');
    await page.getByRole('button', { name: '新建任务' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: '创建分析任务' })).toBeVisible();
  });

  test('should show task list after pre-created data', async ({ page }) => {
    await page.goto('/history');
    const taskCard = page.getByText('测试企业A');
    await expect(taskCard).toBeVisible({ timeout: 10000 });
  });

  test('should render PARTIAL as 部分完成 instead of 执行中', async ({ page }) => {
    await page.goto('/history');

    const partialCompany = page.getByText('测试企业D', { exact: true });
    await expect(partialCompany).toBeVisible({ timeout: 10000 });
    const partialCard = partialCompany.locator('xpath=ancestor::div[contains(@class,\"cursor-pointer\")]');
    await expect(partialCard.getByText('部分完成', { exact: true })).toBeVisible();
    await expect(partialCard.getByText('执行中', { exact: true })).toHaveCount(0);
  });

  test('should click a task and navigate to detail', async ({ page }) => {
    await page.goto('/history');
    const taskCard = page.getByText('测试企业A');
    await expect(taskCard).toBeVisible({ timeout: 10000 });
    await taskCard.click();
    await expect(page).toHaveURL(/\/tasks\/[a-f0-9-]{36}/);
  });
});
