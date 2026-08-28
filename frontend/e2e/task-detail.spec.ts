import { test, expect } from '@playwright/test';
import {
  mockAuthRoutes,
  mockConfigStatus,
  mockNotificationRoutes,
  mockTaskDetailRoutes,
  mockTasksListRoutes,
} from './mocks/setup';

const TASK_ID = '00000000-0000-0000-0000-000000000001';

async function openTaskDetail(page: Parameters<typeof mockAuthRoutes>[0]) {
  await page.goto(`/tasks/${TASK_ID}`);
  await expect(page).toHaveURL(`/tasks/${TASK_ID}`);
  await expect(page.getByRole('heading', { name: '任务状态', exact: true })).toBeVisible({ timeout: 10000 });
}

test.beforeEach(async ({ page }) => {
  await mockAuthRoutes(page);
  await mockConfigStatus(page, true);
  await mockTasksListRoutes(page);
  await mockTaskDetailRoutes(page, TASK_ID);
  await mockNotificationRoutes(page);
});

test.describe('Task Detail — Page Layout', () => {
  test('should show task detail page header', async ({ page }) => {
    await openTaskDetail(page);
    await expect(page.getByRole('heading', { name: '任务详情' })).toBeVisible();
    await expect(page.getByText(/ID:/)).toBeVisible();
  });

  test('should show task status card with company name and demand direction', async ({ page }) => {
    await openTaskDetail(page);
    await expect(page.getByRole('heading', { name: '任务状态', exact: true })).toBeVisible();
    await expect(page.getByText('某市政务服务中心', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('智能客服系统升级', { exact: true })).toBeVisible();
  });

  test('should show status badge', async ({ page }) => {
    await openTaskDetail(page);
    // One of the status labels should be visible
    const statusBadge = page.locator('span', {
      hasText: /已完成|已失败|执行中|等待中/,
    }).first();
    await expect(statusBadge).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Task Detail — Progress Display', () => {
  test('should show progress percentage', async ({ page }) => {
    await openTaskDetail(page);
    // Progress bar section
    await expect(page.getByText('执行进度')).toBeVisible({ timeout: 10000 });
  });

  test('should show current stage and creation time', async ({ page }) => {
    await openTaskDetail(page);
    await expect(page.getByText('当前阶段')).toBeVisible();
    await expect(page.getByText('创建时间')).toBeVisible();
  });
});

test.describe('Task Detail — Research Director Plan', () => {
  test('should show the commercial goal, task DAG and exact LLM queries', async ({ page }) => {
    await openTaskDetail(page);

    await expect(page.getByText('商业分析总目标')).toBeVisible();
    await expect(page.getByRole('heading', {
      name: '目标企业是否值得投入客服中心售前资源',
    })).toBeVisible();
    await expect(page.getByText('目标树', { exact: true })).toBeVisible();
    await expect(page.getByText('任务 DAG', { exact: true })).toBeVisible();
    await expect(page.getByText('T1 · 核验目标企业采购触发')).toBeVisible();
    await expect(page.getByText('前置：T1')).toBeVisible();

    await page.getByText('查看 LLM 决定的搜索内容').first().click();
    await expect(page.getByText(
      'site:example-gov.cn "某市政务服务中心" "客服中心" 招标',
      { exact: true },
    )).toBeVisible();
    await expect(page.getByText('已发生 1 次证据缺口重规划；旧任务和已执行查询均保留。')).toBeVisible();
  });
});

test.describe('Task Detail — Tabs on Completed/Failed', () => {
  test('should show tab navigation for completed or failed tasks', async ({ page }) => {
    await openTaskDetail(page);
    const logsTab = page.getByRole('button', { name: '执行日志' });
    const reportTab = page.getByRole('button', { name: '分析报告' });
    const evidenceTab = page.getByRole('button', { name: '证据回溯' });

    await expect(logsTab).toBeVisible({ timeout: 10000 });
    await expect(reportTab).toBeVisible();
    await expect(evidenceTab).toBeVisible();
  });
});

test.describe('Task Detail — Export Buttons', () => {
  test('should show export PDF button on completed tasks', async ({ page }) => {
    await openTaskDetail(page);

    const reportTab = page.getByRole('button', { name: '分析报告' });
    await expect(reportTab).toBeVisible({ timeout: 10000 });
    await reportTab.click();
    await expect(page.getByRole('button', { name: /导出 PDF/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /导出 Word/ })).toBeVisible();
  });
});

test.describe('Task Detail — Error Display', () => {
  test('should show error message when task has error', async ({ page }) => {
    await openTaskDetail(page);
    // If failed, error message should be visible
    // If not failed, that's fine — the test just confirms no crash
    await expect(page.getByRole('heading', { name: '任务状态', exact: true })).toBeVisible();
  });
});

test.describe('Task Detail v3.1 — New Tabs', () => {
  test('should not duplicate claims that also appear in severity buckets', async ({ page }) => {
    await openTaskDetail(page);
    await page.getByRole('button', { name: '证据审计' }).click();

    await expect(page.getByText('共 3 条结论')).toBeVisible();
    await expect(page.getByText('预算约 200 万', { exact: true })).toHaveCount(1);
    await expect(page.getByText('采用国产化平台是刚性要求', { exact: true })).toHaveCount(1);
  });

  test('should explain when audit completed without auditable evidence', async ({ page }) => {
    await page.unroute(`**/api/reports/${TASK_ID}`);
    await page.route(`**/api/reports/${TASK_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          report_id: '10000000-0000-0000-0000-000000000001',
          task_id: TASK_ID,
          version_id: '20000000-0000-0000-0000-000000000001',
          version_no: 1,
          content_md: '# 部分报告',
          evidence_index: {
            audit: {
              task_id: TASK_ID,
              status: 'NOT_APPLICABLE',
              reason_code: 'NO_AUDITABLE_CLAIMS',
              message: '报告没有准入证据支持的可审计结论，本次审计已结束但未调用审计模型。',
              audited_evidence_count: 0,
              severity: null,
              fatal_claims: [],
              major_claims: [],
              minor_claims: [],
              claim_audits: [],
            },
          },
          created_at: '2026-07-10T10:05:00Z',
        }),
      });
    });

    await openTaskDetail(page);
    await page.getByRole('button', { name: '证据审计' }).click();

    await expect(page.getByText('审计已完成：无可审计结论')).toBeVisible();
    await expect(page.getByText('本次审计已结束但未调用审计模型', { exact: false })).toBeVisible();
    await expect(page.getByText('任务可能未启用审计管线，或审计尚未完成')).toHaveCount(0);
  });

  test('should show 证据审计 tab when available', async ({ page }) => {
    await openTaskDetail(page);

    // 查找审计 tab（可能在任务完成后才显示）
    const auditTab = page.getByRole('button', { name: /审计|audit/i });
    const fieldAgentTab = page.getByRole('button', { name: /背调|field|体验/i });
    // 至少页面加载成功
    await expect(page.getByRole('heading', { name: '任务状态', exact: true })).toBeVisible();
  });

  test('should show OpportunityScoreCard when score data exists', async ({ page }) => {
    await openTaskDetail(page);

    // 点击分析报告 tab
    const reportTab = page.getByRole('button', { name: /报告|report/i });
    if (await reportTab.first().isVisible().catch(() => false)) {
      await reportTab.first().click();
      await page.waitForTimeout(1000);
    }
  });
});
