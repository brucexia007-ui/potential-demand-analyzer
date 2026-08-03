import { expect, test, type Page } from '@playwright/test';
import { mockNotificationRoutes, mockSkillsRoutes } from './mocks/setup';


const ACCOUNT_ID = '91000000-0000-0000-0000-000000000001';
const OPPORTUNITY_ID = '92000000-0000-0000-0000-000000000001';
const HYPOTHESIS_ID = '93000000-0000-0000-0000-000000000001';
const IMPORT_JOB_ID = '94000000-0000-0000-0000-000000000001';
const SUBSCRIPTION_ID = '95000000-0000-0000-0000-000000000001';


function importJob(status = 'PREVIEWED') {
  return {
    id: IMPORT_JOB_ID, source_type: 'GITHUB', repo_url: 'https://github.com/example/expert-skills',
    commit_sha: 'a'.repeat(40), path: 'skills/account-expert', request_hash: 'b'.repeat(64),
    archive_snapshot_path: null, snapshot_hash: 'c'.repeat(64), source_snapshot_path: 'immutable/source',
    converted_snapshot_path: 'immutable/converted', merge_snapshot_path: null,
    conversion_result: {
      source_format: 'CODEX_CLAUDE', source_snapshot_hash: 'c'.repeat(64), output_files: {},
      missing_required: [], inferred_fields: [], removed_fields: ['frontmatter.allowed-tools'],
      issues: [{ code: 'FRONTMATTER_FIELD_REMOVED', severity: 'WARNING', message: '外部工具声明已移除', path: 'SKILL.md' }],
      license_status: 'DECLARED', license_value: 'MIT', publishable: true,
    },
    merge_result: {}, diff_text: '- allowed-tools: Bash\n+ metadata:\n+   execution_phase: research',
    mock_result: {}, status, dispatch_attempt: 1, error_code: null, error_message: null,
    expires_at: '2026-07-23T00:00:00Z', started_at: '2026-07-22T00:00:00Z',
    finished_at: '2026-07-22T00:00:02Z', confirmed_at: null, imported_at: null,
    skill_id: null, version_id: null, upstream_source_id: null,
    created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:02Z',
  };
}


async function mockSkillImport(page: Page) {
  await page.route('**/api/skills/imports/github/preview', async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      repo_url: 'https://github.com/example/expert-skills', commit_sha: 'a'.repeat(40),
    });
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(importJob()) });
  });
  await page.route(`**/api/skills/imports/${IMPORT_JOB_ID}/mock`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      job: importJob('MOCKED'), compiled_name: 'account-expert', execution_phase: 'research',
      synthetic_questions: ['客户为什么现在行动？'], planned_sources: ['客户官网'],
      expected_output_fields: ['finding'], network_calls: 0, model_calls: 0, filesystem_writes: 0,
    }) });
  });
  await page.route(`**/api/skills/imports/${IMPORT_JOB_ID}/confirm`, async (route) => {
    expect(route.request().postDataJSON()).toEqual({ confirmed: true, conflict_action: 'CREATE_NEW' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      job: { ...importJob('IMPORTED'), skill_id: '96000000-0000-0000-0000-000000000001', version_id: '97000000-0000-0000-0000-000000000001' },
      created_skill: true,
      skill: {
        id: '96000000-0000-0000-0000-000000000001', name: 'account-expert', display_name: 'account-expert',
        description: '专家客户研究', scope: 'WORKSPACE', status: 'DRAFT', editable: true,
        current_version_id: null, latest_version: null, created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
      },
      version: {
        id: '97000000-0000-0000-0000-000000000001', version: 1, status: 'COMPILED', content_hash: 'd'.repeat(64),
        compiled_spec: { name: 'account-expert', description: '专家客户研究', version: 1 },
        compiled_at: '2026-07-22T00:00:00Z', published_at: null, created_at: '2026-07-22T00:00:00Z',
      },
    }) });
  });
}


async function mockRadar(page: Page) {
  let subscribed = false;
  const subscription = {
    id: SUBSCRIPTION_ID, target_account_id: ACCOUNT_ID, capability_profile_id: null,
    root_skill_name: 'pilot-opportunity', topics: ['PROCUREMENT', 'POLICY', 'CONTRACT_WINDOW'],
    frequency: 'WEEKLY', timezone_name: 'Asia/Shanghai', max_external_calls: 20,
    max_input_tokens: 120000, status: 'ACTIVE', next_run_at: '2026-07-29T00:00:00Z',
    last_run_at: '2026-07-22T00:00:00Z', created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
  };
  await page.route('**/api/target-accounts/*/workbench', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      account: { id: ACCOUNT_ID, input_name: '未来制造', official_name: '未来制造股份有限公司', website: null, credit_code: null, industry: '制造', region: '苏州', stock_code: null, parent_id: null, status: 'CONFIRMED', created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z' },
      counts: { tasks: 0, claims: 1, gate_decisions: 1, hypotheses: 0, opportunities: 0, pending_actions: 0 },
      tasks: [], claims: [{ id: 'claim-1', workspace_id: 'workspace-1', task_id: 'task-1', report_version_id: null, claim_text: '客户新增数据治理采购信号', claim_type: 'FACT', opportunity_effect: 'trigger', status: 'SUPPORTED', confidence: 0.88, first_seen_at: '2026-07-22T00:00:00Z', last_verified_at: '2026-07-22T00:00:00Z', expires_at: null, evidence_count: 1 }],
      latest_gate: { id: 'gate-1', decision: 'HYPOTHESIS', gate_level: 'G4', analysis_as_of_date: '2026-07-22T00:00:00Z', summary: { reasons: ['新增采购信号待客户验证'] } },
      hypotheses: [], opportunities: [],
    }) });
  });
  await page.route('**/api/opportunities/qualification-frameworks', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[]}' }));
  await page.route('**/api/opportunities/target-accounts/*/stakeholders', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[]}' }));
  await page.route('**/api/capability-profiles?*', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[]}' }));
  await page.route('**/api/watchlist/subscriptions*', async (route) => {
    if (route.request().method() === 'POST') {
      subscribed = true;
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(subscription) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: subscribed ? [subscription] : [], total: subscribed ? 1 : 0 }) });
  });
  await page.route(`**/api/watchlist/subscriptions/${SUBSCRIPTION_ID}/runs*`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{
      id: '98000000-0000-0000-0000-000000000001', subscription_id: SUBSCRIPTION_ID,
      target_account_id: ACCOUNT_ID, previous_run_id: null, task_id: '99000000-0000-0000-0000-000000000001',
      scheduled_for: '2026-07-22T00:00:00Z', analysis_as_of_date: '2026-07-22', status: 'COMPLETED',
      budget: { max_external_calls: 20 }, usage: { external_calls: 4, input_tokens: 8000 },
      change_summary: { has_material_change: true, new_evidence_count: 1, changed_claim_count: 1, gate_level: 'G4', categories: { claim: ['claim-hash'] } },
      error_code: null, error_message: null, started_at: '2026-07-22T00:00:00Z', finished_at: '2026-07-22T00:03:00Z', created_at: '2026-07-22T00:00:00Z',
    }], total: 1 }) });
  });
}


async function mockFeedbackAndDashboard(page: Page) {
  const opportunity = {
    id: OPPORTUNITY_ID, workspace_id: 'workspace-1', target_account_id: ACCOUNT_ID,
    source_hypothesis_id: HYPOTHESIS_ID, title: '数据治理正式商机', stage: 'DISCOVERY',
    owner_user_id: 'user-1', amount: null, currency: null, amount_source: 'UNSPECIFIED',
    probability: 0.4, expected_close_date: null, closed_at: null, close_reason: null,
    created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
  };
  let records: Record<string, unknown>[] = [];
  await page.route(`**/api/opportunities/${OPPORTUNITY_ID}`, async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opportunity) }));
  await page.route(`**/api/opportunities/${OPPORTUNITY_ID}/history`, async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'history-1', opportunity_id: OPPORTUNITY_ID, from_stage: null, to_stage: 'DISCOVERY', reason: '客户验证通过', request_key: 'created', changed_by: 'user-1', created_at: '2026-07-22T00:00:00Z' }] }) }));
  await page.route('**/api/watchlist/feedback/reasons*', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[]}' }));
  await page.route('**/api/watchlist/feedback*', async (route) => {
    if (route.request().method() === 'POST') {
      const input = route.request().postDataJSON();
      records = [{ id: 'feedback-1', ...input, outcome_data: input.outcome, recorded_by: 'user-1', created_at: input.effective_at }];
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ feedback: records[0], created: true }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: records }) });
  });
  await page.route('**/api/watchlist/dashboard?*', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    generated_at: '2026-07-22T00:10:00Z', cohort_basis: 'RESEARCH_TASK_CREATED_AT', filters: {},
    funnel: [
      ['RESEARCHED_ACCOUNTS', '研究客户', 10, null], ['G1', 'G1 身份可信', 9, 0.9], ['G2', 'G2 事实可信', 8, 0.89],
      ['G3', 'G3 需求信号', 6, 0.75], ['G4', 'G4 可验证机会', 4, 0.67], ['G5', 'G5 强机会', 2, 0.5],
      ['GX', 'GX 不建议推进', 1, null], ['HYPOTHESES', '商机假设', 4, 1], ['SALES_ACCEPTED', '销售接受', 3, 0.75],
      ['CUSTOMER_VALIDATED', '客户验证', 2, 0.67], ['OPPORTUNITIES', '正式商机', 2, 1], ['WON', '成交', 1, 0.5],
    ].map(([key, label, count, conversion_from_previous]) => ({ key, label, count, conversion_from_previous })),
    outcomes: { signal_accepted: 3, signal_rejected: 1, customer_validated: 2, customer_invalidated: 1, no_opportunity: 1, identification_error: 0, signal_acceptance_rate: 0.75, customer_validation_rate: 0.667 },
    amounts: { by_currency: [], missing_or_unconfirmed_count: 2 },
    execution: { external_call_count: 20, settled_call_count: 18, input_tokens: 10000, output_tokens: 5000, average_call_latency_ms: 800, average_research_duration_seconds: 300, settled_costs: [{ currency: 'CNY', settled_amount: '12.50' }], saved_labor_hours: null, saved_labor_hours_status: 'NOT_CONFIGURED' },
    dwell_times: [{ key: 'HYPOTHESIS_TO_ACCEPTANCE', label: '假设到销售接受', sample_count: 3, average_seconds: 86400 }],
  }) }));
}


test('v3.5 completes reviewed Skill import, radar change, feedback and dashboard flow', async ({ page }) => {
  await mockNotificationRoutes(page);
  await mockSkillsRoutes(page);
  await mockSkillImport(page);
  await mockRadar(page);
  await mockFeedbackAndDashboard(page);

  await page.goto('/settings/skills');
  await page.getByRole('button', { name: '导入外部 Skill' }).click();
  await page.getByLabel('仓库 URL').fill('https://github.com/example/expert-skills');
  await page.getByRole('textbox', { name: 'Commit SHA', exact: true }).fill('a'.repeat(40));
  await page.getByRole('button', { name: '获取并安全检查' }).click();
  await expect(page.getByText('静态安全检查可继续')).toBeVisible();
  await page.getByRole('button', { name: '继续查看转换' }).click();
  await page.getByRole('button', { name: '查看 Diff' }).click();
  await page.getByRole('button', { name: '进入 Mock' }).click();
  await page.getByRole('button', { name: '执行 Mock' }).click();
  await expect(page.getByText('网络调用').locator('..')).toContainText('0');
  await page.getByRole('button', { name: '人工确认' }).click();
  await page.getByLabel(/我已审阅风险/).check();
  await page.getByRole('button', { name: '确认导入' }).click();
  await expect(page.getByText(/已导入为本地草稿/)).toBeVisible();

  await page.goto(`/customers/${ACCOUNT_ID}`);
  await page.getByRole('button', { name: '建立雷达订阅' }).click();
  await page.getByRole('button', { name: '刷新结果' }).click();
  await expect(page.getByText('关键结论 1')).toBeVisible();
  await expect(page.getByText('最新 Gate G4')).toBeVisible();

  await page.goto(`/opportunities/${OPPORTUNITY_ID}`);
  await page.getByLabel('核验结果').fill('客户确认问题真实且需要继续推进');
  await page.getByRole('button', { name: '记录业务反馈' }).click();
  await expect(page.getByText('客户确认问题真实且需要继续推进')).toBeVisible();

  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: '证据化商机推进漏斗' })).toBeVisible();
  await expect(page.getByText('未录入或仅为估算：')).toBeVisible();
  await expect(page.getByText('未配置人工基线')).toBeVisible();
  await expect(page.getByText('GX：证据支持不建议推进')).toBeVisible();
});
