import { expect, test, type Page } from '@playwright/test';
import { mockNotificationRoutes } from './mocks/setup';


const ACCOUNT_ID = '81000000-0000-0000-0000-000000000001';
const OPPORTUNITY_ID = '82000000-0000-0000-0000-000000000001';
const DELIVERY_ID = '83000000-0000-0000-0000-000000000001';


async function mockV34Customer(page: Page) {
  await page.route(`**/api/target-accounts/${ACCOUNT_ID}/workbench`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        account: {
          id: ACCOUNT_ID, input_name: '未来制造', official_name: '未来制造股份有限公司',
          website: 'https://example.com', credit_code: null, industry: '制造', region: '苏州',
          stock_code: null, parent_id: null, status: 'CONFIRMED',
          created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
        },
        counts: { tasks: 0, claims: 0, gate_decisions: 1, hypotheses: 1, opportunities: 1, pending_actions: 0 },
        tasks: [], claims: [], latest_gate: null, hypotheses: [],
        opportunities: [{
          id: OPPORTUNITY_ID,
          source_hypothesis_id: '84000000-0000-0000-0000-000000000001',
          title: '数据治理平台建设正式商机', stage: 'DISCOVERY',
          owner_user_id: '85000000-0000-0000-0000-000000000001',
          amount: '1200000.00', currency: 'CNY', amount_source: 'CUSTOMER_CONFIRMED',
          probability: 0.4, expected_close_date: '2026-11-20', closed_at: null, close_reason: null,
          created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
        }],
      }),
    });
  });
  await page.route('**/api/opportunities/qualification-frameworks', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route(`**/api/opportunities/target-accounts/${ACCOUNT_ID}/stakeholders`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [{
        id: '86000000-0000-0000-0000-000000000001', workspace_id: '87000000-0000-0000-0000-000000000001',
        target_account_id: ACCOUNT_ID, opportunity_id: OPPORTUNITY_ID, role_type: 'BUSINESS_OWNER',
        full_name: '张负责人', role_title: '数据平台主管', department: '数据管理部', influence: 'HIGH',
        attitude: 'SUPPORTIVE', goals: '完成数据标准统一', concerns: '业务连续性',
        relationship_strength: 'MEDIUM', truth_status: 'SALES_JUDGMENT', source_claim_id: null,
        communication_strategy: '围绕合规和效率开展需求访谈', status: 'ACTIVE',
        created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
      }] }),
    });
  });
  await page.route(`**/api/opportunities/${OPPORTUNITY_ID}/competitors`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route(`**/api/opportunities/${OPPORTUNITY_ID}/value-hypotheses`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route('**/api/capability-profiles?**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
}


test('v3.4 customer operations expose formal opportunity and require webhook preview confirmation', async ({ page }) => {
  await mockNotificationRoutes(page);
  await mockV34Customer(page);
  let confirmedRequest: Record<string, unknown> | null = null;
  await page.route(`**/api/integrations/target-accounts/${ACCOUNT_ID}/exports/json`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'Content-Disposition': `attachment; filename="business-export-${ACCOUNT_ID}.json"` },
      body: JSON.stringify({ schema_version: 'business-export/v1', account: { id: ACCOUNT_ID } }),
    });
  });
  await page.route(`**/api/integrations/target-accounts/${ACCOUNT_ID}/webhook-previews`, async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ destination_url: 'https://hooks.example.com/business' });
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: DELIVERY_ID, schema_version: 'business-export/v1', target_account_id: ACCOUNT_ID,
        idempotency_key: 'business-export:test', destination_display: 'https://hooks.example.com/business',
        status: 'PREVIEWED', expires_at: '2026-07-22T01:00:00Z', confirmed_at: null, completed_at: null,
        attempt_count: 0, http_status: null, failure_code: null, failure_message: null,
        created_at: '2026-07-22T00:45:00Z', updated_at: '2026-07-22T00:45:00Z', created: true,
        payload: {
          schema_version: 'business-export/v1', account: { id: ACCOUNT_ID, official_name: '未来制造股份有限公司' },
          claims: [{ id: 'claim-1' }], hypotheses: [{ id: 'hypothesis-1' }], qualifications: [{ id: 'qualification-1' }],
          actions: [{ id: 'action-1' }], opportunities: [{ id: OPPORTUNITY_ID }],
        },
      }),
    });
  });
  await page.route(`**/api/integrations/webhook-deliveries/${DELIVERY_ID}/confirm-and-send`, async (route) => {
    confirmedRequest = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: DELIVERY_ID, schema_version: 'business-export/v1', target_account_id: ACCOUNT_ID,
        idempotency_key: 'business-export:test', destination_display: 'https://hooks.example.com/business',
        status: 'SUCCEEDED', expires_at: '2026-07-22T01:00:00Z', confirmed_at: '2026-07-22T00:50:00Z',
        completed_at: '2026-07-22T00:50:01Z', attempt_count: 1, http_status: 202,
        failure_code: null, failure_message: null, created_at: '2026-07-22T00:45:00Z', updated_at: '2026-07-22T00:50:01Z',
      }),
    });
  });

  await page.goto(`/customers/${ACCOUNT_ID}`);
  await expect(page.getByTestId('formal-opportunity')).toContainText('数据治理平台建设正式商机');
  await expect(page.getByText('张负责人')).toBeVisible();
  await expect(page.getByTestId('business-export')).toBeVisible();

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 JSON' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain('business-export');

  await page.getByRole('button', { name: '推送 Webhook' }).click();
  await page.getByLabel('HTTPS Webhook 地址').fill('https://hooks.example.com/business');
  await page.getByRole('button', { name: '生成发送预览' }).click();
  await expect(page.getByText('待发送：business-export/v1')).toBeVisible();
  await expect(page.getByText('Claim：1')).toBeVisible();
  await expect(page.getByRole('button', { name: '确认并发送一次' })).toBeDisabled();
  await page.getByLabel('HMAC 签名密钥（至少 32 字节）').fill('s'.repeat(32));
  await page.getByLabel(/我已检查目标地址和完整载荷/).check();
  await page.getByRole('button', { name: '确认并发送一次' }).click();

  await expect(page.getByText('发送状态：SUCCEEDED')).toBeVisible();
  expect(confirmedRequest).toMatchObject({
    confirmed: true,
    destination_url: 'https://hooks.example.com/business',
    signing_secret: 's'.repeat(32),
  });
});
