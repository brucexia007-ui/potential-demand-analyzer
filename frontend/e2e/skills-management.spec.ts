import { expect, test } from '@playwright/test';
import { mockNotificationRoutes, mockSkillsRoutes } from './mocks/setup';

test.describe('Skill V2 management', () => {
  test.beforeEach(async ({ page }) => {
    await mockSkillsRoutes(page);
    await mockNotificationRoutes(page);
    await page.goto('/settings/skills');
  });

  test('shows system and workspace skills without legacy toggles', async ({ page }) => {
    await expect(page.getByText('标准商机研究')).toBeVisible();
    await expect(page.getByText('系统只读')).toBeVisible();
    await expect(page.getByText('行业客户研究')).toBeVisible();
    await expect(page.getByRole('button', { name: /启用|禁用/ })).toHaveCount(0);
  });

  test('offers guided and SKILL.md authoring modes', async ({ page }) => {
    await page.getByRole('button', { name: '新建 Skill' }).click();
    await expect(page.getByText('引导模式', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'SKILL.md' }).click();
    await expect(page.locator('textarea')).toContainText('metadata:');
    await expect(page.locator('textarea')).toContainText('version: "1"');
  });

  test('shows complete system SKILL.md without destructive mode conversion', async ({ page }) => {
    await page.getByRole('button', { name: '查看' }).first().click();
    const source = page.locator('textarea');
    await expect(source).toContainText('## Questions');
    await expect(source).toContainText('## Dependencies');
    await page.getByRole('button', { name: '引导模式' }).click();
    await page.getByRole('button', { name: 'SKILL.md' }).click();
    await expect(source).toContainText('matching-product-capabilities@1');
  });

  test('previews latest version without external execution', async ({ page }) => {
    await page.getByRole('button', { name: 'Dry Run' }).last().click();
    await expect(page.getByText('Dry Run 执行预览')).toBeVisible();
    await expect(page.getByText('SEARCH: 客户官网')).toBeVisible();
    await expect(page.getByText('本次预演未调用模型、搜索、抓取或外部文件。')).toBeVisible();
  });

  test('requires a passing golden case evaluation before publishing', async ({ page }) => {
    await expect(page.getByRole('button', { name: '发布 v2' })).toHaveCount(0);
    await page.getByRole('button', { name: '评测 v2' }).click();
    await page.getByLabel('真实样本问题').fill('研究客户为什么现在需要行动');
    await page.getByLabel('实际回答的问题（每行一项）').fill('客户为什么现在需要行动');
    await page.getByLabel('实际使用的信源（每行一项）').fill('客户官网');
    await page.getByLabel('实际报告章节（每行一项）').fill('关键发现');
    await page.getByLabel('实际证据数').fill('3');
    await page.getByLabel('关键结论数').fill('1');
    await page.getByLabel('已引用结论数').fill('1');
    await page.getByLabel('人工盲评分').fill('90');
    await page.getByRole('button', { name: '保存黄金用例' }).click();
    await expect(page.getByText('v2 发布门黄金用例').first()).toBeVisible();
    await page.getByRole('button', { name: '运行 1 条用例' }).click();
    await expect(page.getByText('最近评测：已通过')).toBeVisible();
    await page.getByRole('button', { name: '返回' }).click();
    await page.getByRole('button', { name: '发布 v2' }).click();
    await expect(page.getByText(/已发布/).first()).toBeVisible();
  });
});
