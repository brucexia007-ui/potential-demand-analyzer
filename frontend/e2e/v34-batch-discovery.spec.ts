import { expect, test } from '@playwright/test';
import { mockNotificationRoutes } from './mocks/setup';

const BATCH_ID = '72000000-0000-0000-0000-000000000001';

test('batch discovery shows isolated disambiguation and business pipeline states', async ({ page }) => {
  await mockNotificationRoutes(page);
  await page.route(`**/api/batches/${BATCH_ID}?**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        batch_id: BATCH_ID,
        name: '自动线索发现批次',
        status: 'PARTIAL',
        root_skill_name: 'pilot-opportunity',
        research_mode: 'OPPORTUNITY_DISCOVERY',
        capability_profile_id: '73000000-0000-0000-0000-000000000001',
        total_tasks: 2,
        completed_tasks: 1,
        failed_tasks: 1,
        cancelled_tasks: 0,
        paused_tasks: 0,
        running_tasks: 0,
        partial_tasks: 0,
        paused: false,
        started_at: '2026-07-22T08:00:00Z',
        finished_at: '2026-07-22T08:30:00Z',
        error_message: null,
        created_at: '2026-07-22T08:00:00Z',
        updated_at: '2026-07-22T08:30:00Z',
        tasks: [],
        tasks_total: 2,
        tasks_page: 1,
        tasks_page_size: 20,
        import_rows_total: 3,
        accepted_rows: 2,
        rejected_rows: 1,
        import_rows: [
          {
            row_index: 0,
            company_name: '同名集团',
            demand_direction: '自动发现',
            validation_status: 'needs_disambiguation',
            error_message: '同名企业存在多个候选，必须补充消歧字段',
            task_id: null,
            candidate_ids: ['a', 'b'],
            target_account_id: null,
            target_status: 'NEEDS_DISAMBIGUATION',
            research_status: 'NOT_CREATED',
            signal_status: 'NOT_CREATED',
            product_match_status: 'NOT_CREATED',
            hypothesis_status: 'NOT_CREATED',
          },
          {
            row_index: 1,
            company_name: '已完成企业',
            demand_direction: '自动发现',
            validation_status: 'valid',
            error_message: null,
            task_id: '74000000-0000-0000-0000-000000000001',
            candidate_ids: [],
            target_account_id: '75000000-0000-0000-0000-000000000001',
            target_status: 'CONFIRMED',
            research_status: 'COMPLETED',
            signal_status: 'FOUND',
            product_match_status: 'MATCHED',
            hypothesis_status: 'PENDING_SALES_REVIEW',
          },
          {
            row_index: 2,
            company_name: '无机会企业',
            demand_direction: '自动发现',
            validation_status: 'valid',
            error_message: null,
            task_id: '74000000-0000-0000-0000-000000000002',
            candidate_ids: [],
            target_account_id: '75000000-0000-0000-0000-000000000002',
            target_status: 'UNRESOLVED',
            research_status: 'FAILED',
            signal_status: 'NONE',
            product_match_status: 'NONE',
            hypothesis_status: 'NONE',
          },
        ],
      }),
    });
  });

  await page.goto(`/batches/${BATCH_ID}`);

  await expect(page.getByTestId('batch-discovery-progress')).toBeVisible();
  await expect(page.getByTestId('batch-row-0')).toContainText('候选主体 2 个');
  await expect(page.getByTestId('batch-row-0')).toContainText('NEEDS_DISAMBIGUATION');
  await expect(page.getByTestId('batch-row-1')).toContainText('MATCHED');
  await expect(page.getByTestId('batch-row-1')).toContainText('PENDING_SALES_REVIEW');
  await expect(page.getByText('可执行行').locator('..')).toContainText('2');
  await expect(page.getByText('待修正行').locator('..')).toContainText('1');
});
