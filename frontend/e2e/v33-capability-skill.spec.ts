import { expect, test, type Page } from '@playwright/test';
import { mockNotificationRoutes, mockSkillsRoutes } from './mocks/setup';


const PROFILE_ID = '51000000-0000-0000-0000-000000000001';
const PRODUCT_ID = '52000000-0000-0000-0000-000000000001';
const REPORT_ID = '53000000-0000-0000-0000-000000000001';
const VERSION_ID = '54000000-0000-0000-0000-000000000001';
const THREAD_ID = '55000000-0000-0000-0000-000000000001';
const G5_TASK_ID = '56000000-0000-0000-0000-000000000001';
const GX_TASK_ID = '56000000-0000-0000-0000-000000000002';
const CLARIFICATION_ID = '58000000-0000-0000-0000-000000000001';
const FOLLOW_UP_TASK_ID = '5b000000-0000-0000-0000-000000000001';
const FOLLOW_UP_RUN_ID = '5c000000-0000-0000-0000-000000000001';


async function mockCapabilityRoutes(page: Page) {
  const profile = {
    id: PROFILE_ID,
    workspace_id: '50000000-0000-0000-0000-000000000001',
    name: '银行智能服务能力档案',
    legal_entity_name: '示例科技股份有限公司',
    description: '用于银行客户研究与智能服务产品匹配',
    is_default: true,
    status: 'ACTIVE',
    created_at: '2026-07-22T00:00:00Z',
    updated_at: '2026-07-22T00:00:00Z',
  };
  const product = {
    id: PRODUCT_ID,
    workspace_id: profile.workspace_id,
    profile_id: PROFILE_ID,
    name: '智能服务研究平台',
    product_line: 'AI 售前产品线',
    version_label: '1.0',
    summary: '支持客户研究、能力缺口识别与证据化商机判断',
    capabilities: [{ name: 'account_research' }],
    constraints: [{ name: '仅支持已授权数据源' }],
    unsuitable_scenarios: [{ name: '无客户需求证据时不得推荐' }],
    differentiators: [{ name: 'OIG 前置裁决' }],
    supported_regions: ['CN'],
    supported_industries: ['银行'],
    status: 'ACTIVE',
    effective_from: '2026-01-01T00:00:00Z',
    effective_to: null,
    created_at: '2026-07-22T00:00:00Z',
    updated_at: '2026-07-22T00:00:00Z',
  };

  await page.route('**/api/capability-profiles?*', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [profile] }) });
  });
  await page.route(new RegExp(`/api/capability-profiles/${PROFILE_ID}$`), async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(profile) });
  });
  await page.route(`**/api/capability-profiles/${PROFILE_ID}/products`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [product] }) });
  });
  for (const collection of ['documents', 'solutions', 'cases', 'qualifications']) {
    await page.route(`**/api/capability-profiles/${PROFILE_ID}/${collection}`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
    });
  }
}


async function mockCompletedOpportunityTask(
  page: Page,
  taskId: string,
  grade: 'G5' | 'GX',
) {
  const isCandidate = grade === 'G5';
  const opportunityCard = isCandidate
    ? [
      '## 商机裁决',
      '',
      '**G5 · 可介入候选**',
      '',
      '当前需求、采购窗口与产品适配均有证据支持。',
      '',
      '### 候选产品',
      '',
      '- 智能服务研究平台 v1.0（ProductFit 82 分）',
      '',
      '仍需销售接受和客户验证，系统不会自动创建正式商机。',
    ].join('\n')
    : [
      '## 商机裁决',
      '',
      '**GX · 暂无明确商机**',
      '',
      '### 硬性阻断',
      '',
      '- 目标地区不在产品交付范围。',
      '',
      '不得创建商机假设，也不得用产品能力反向创造客户需求。',
    ].join('\n');

  await page.route(new RegExp(`/api/tasks/${taskId}$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId,
        company_name: '未来银行股份有限公司',
        demand_direction: '客户研究与商机发现',
        status: 'COMPLETED',
        current_stage: 'REPORT',
        progress: 100,
        error_message: null,
        created_at: '2026-07-22T00:00:00Z',
        updated_at: '2026-07-22T00:10:00Z',
      }),
    });
  });
  await page.route(`**/api/tasks/${taskId}/execution`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId,
        desired_state: 'RUNNING',
        observed_state: 'COMPLETED',
        control_version: 1,
        active_run: { id: '57000000-0000-0000-0000-000000000001', generation: 1, status: 'COMPLETED', started_at: '2026-07-22T00:00:00Z' },
        dimensions: [{ dimension: 'pilot-opportunity', total_units: 6, completed_units: 6, remaining_units: 0, status_counts: { COMPLETED: 6 } }],
        remaining_work_units: 0,
        budget: { reserved_amount: 0, settled_amount: 0, refunded_amount: 0, net_reserved_amount: 0, currencies: [] },
        latest_heartbeat_at: '2026-07-22T00:10:00Z',
        latest_checkpoint: null,
        recovery_count: 0,
        eta: null,
      }),
    });
  });
  await page.route(`**/api/tasks/${taskId}/execution/events/stream?*`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
  });
  await page.route(`**/api/tasks/${taskId}/execution/events?*`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) });
  });
  await page.route(`**/api/tasks/${taskId}/logs`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ logs: [] }) });
  });
  await page.route(`**/api/tasks/${taskId}/clarifications`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route(`**/api/reports/${taskId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        report_id: REPORT_ID,
        task_id: taskId,
        version_id: VERSION_ID,
        version_no: 1,
        content_md: opportunityCard,
        evidence_index: {
          validation: { passed: true, claims_total: 1, claims_valid: 1, violations: [] },
        },
        created_at: '2026-07-22T00:10:00Z',
      }),
    });
  });
  await page.route(`**/api/reports/${REPORT_ID}/views/*`, async (route) => {
    const viewType = route.request().url().split('/').pop() || 'EXECUTIVE_30S';
    const content = viewType === 'OPPORTUNITY_CARD'
      ? opportunityCard
      : `## 30 秒摘要\n\n${isCandidate ? '存在经 OIG 裁决的待验证候选。' : '存在硬阻断，当前不建议推进。'}`;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        view_type: viewType,
        report_id: REPORT_ID,
        version_id: VERSION_ID,
        version_no: 1,
        title: viewType === 'OPPORTUNITY_CARD' ? '商机裁决卡' : '30 秒客户摘要',
        content_md: content,
        sections: [],
        citation_count: 0,
        source_manifest: [{ source_type: 'gate_decision', source_id: `${grade}-decision` }],
        generated_by: 'DETERMINISTIC_ASSET_PROJECTION',
      }),
    });
  });
  await page.route(`**/api/reports/${REPORT_ID}/threads`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [{
          id: THREAD_ID,
          report_id: REPORT_ID,
          bound_version_id: VERSION_ID,
          title: '报告深度讨论',
          status: 'ACTIVE',
          created_at: '2026-07-22T00:10:00Z',
          updated_at: '2026-07-22T00:10:00Z',
        }],
      }),
    });
  });
  await page.route(`**/api/report-threads/${THREAD_ID}/follow-ups`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route(`**/api/report-threads/${THREAD_ID}/messages`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route(`**/api/reports/${REPORT_ID}/drafts`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
}


test.describe('v3.3 capability, Skill and OIG release candidate flow', () => {
  test.beforeEach(async ({ page }) => {
    await mockNotificationRoutes(page);
    await mockSkillsRoutes(page);
  });

  test('exposes the active product and published root Skill before research', async ({ page }) => {
    await mockCapabilityRoutes(page);

    await page.goto('/capabilities');
    await expect(page.getByRole('heading', { name: '企业能力中心' })).toBeVisible();
    await expect(page.getByText('银行智能服务能力档案')).toBeVisible();
    await page.getByRole('link', { name: '管理产品' }).click();
    await expect(page.getByRole('heading', { name: '智能服务研究平台' })).toBeVisible();
    await expect(page.getByText('account_research')).toBeVisible();
    await expect(page.getByText('无客户需求证据时不得推荐')).toBeVisible();

    await page.goto('/settings/skills');
    await expect(page.getByText('标准商机研究')).toBeVisible();
    await expect(page.getByText('pilot-opportunity')).toBeVisible();
    await expect(page.getByText('已发布').first()).toBeVisible();

    await page.goto('/');
    await page.getByRole('button', { name: '手动填写' }).click();
    await expect(page.locator('#runtime-skill')).toHaveValue('pilot-opportunity');
  });

  test('shows a G5 candidate as a hypothesis rather than a formal opportunity', async ({ page }) => {
    await mockCompletedOpportunityTask(page, G5_TASK_ID, 'G5');

    await page.goto(`/tasks/${G5_TASK_ID}`);
    await page.getByRole('button', { name: '分析报告' }).click();
    await page.getByRole('button', { name: '商机卡' }).click();

    await expect(page.getByText('G5 · 可介入候选')).toBeVisible();
    await expect(page.getByText(/智能服务研究平台 v1\.0/)).toBeVisible();
    await expect(page.getByText('仍需销售接受和客户验证，系统不会自动创建正式商机。')).toBeVisible();
  });

  test('shows formal report citation count instead of internal source manifest size', async ({ page }) => {
    await mockCompletedOpportunityTask(page, G5_TASK_ID, 'G5');

    await page.goto(`/tasks/${G5_TASK_ID}`);
    await page.getByRole('button', { name: '分析报告' }).click();

    await expect(page.getByText('正式版本 V1 · 报告引用证据 0 条')).toBeVisible();
    await expect(page.getByText('正式版本 V1 · 1 个来源')).toHaveCount(0);
  });

  test('shows the ProductFit hard blocker and does not promote GX', async ({ page }) => {
    await mockCompletedOpportunityTask(page, GX_TASK_ID, 'GX');

    await page.goto(`/tasks/${GX_TASK_ID}`);
    await page.getByRole('button', { name: '分析报告' }).click();
    await page.getByRole('button', { name: '商机卡' }).click();

    await expect(page.getByText('GX · 暂无明确商机')).toBeVisible();
    await expect(page.getByText('目标地区不在产品交付范围。')).toBeVisible();
    await expect(page.getByText('不得创建商机假设，也不得用产品能力反向创造客户需求。')).toBeVisible();
    await expect(page.getByText('G5 · 可介入候选')).toHaveCount(0);
  });

  test('keeps the task paused for a partial clarification and resumes only after explicit assumption approval', async ({ page }) => {
    await mockCompletedOpportunityTask(page, G5_TASK_ID, 'G5');
    const submitted: Record<string, unknown>[] = [];
    let clarificationOpen = true;
    const clarification = {
      id: CLARIFICATION_ID,
      task_id: G5_TASK_ID,
      phase: 'IN_EXECUTION',
      category: 'RESEARCH_SCOPE',
      materiality: 'BLOCKING',
      question: '本次研究是否包含海外子公司？',
      options: [
        { code: 'DOMESTIC_ONLY', label: '仅境内主体', impact: '缩小范围，结论更快形成。' },
        { code: 'INCLUDE_OVERSEAS', label: '包含海外主体', impact: '增加多语言检索和主体消歧。' },
      ],
      recommended_option: 'DOMESTIC_ONLY',
      impact: '研究范围会改变搜索词、证据边界和报告结论。',
      status: 'OPEN',
      control_version: 3,
    };
    await page.unroute(`**/api/tasks/${G5_TASK_ID}/clarifications`);
    await page.route(`**/api/tasks/${G5_TASK_ID}/clarifications`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(clarificationOpen ? [clarification] : []),
      });
    });
    await page.route(`**/api/clarifications/${CLARIFICATION_ID}/answer`, async (route) => {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      submitted.push(payload);
      const resumed = payload.finalize === true;
      if (resumed) clarificationOpen = false;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          request_id: CLARIFICATION_ID,
          response_id: `59000000-0000-0000-0000-00000000000${submitted.length}`,
          control_version: resumed ? 4 : 3,
          queued_stage_run_id: resumed ? '5a000000-0000-0000-0000-000000000001' : null,
          resumed,
          idempotent: false,
        }),
      });
    });

    await page.goto(`/tasks/${G5_TASK_ID}`);
    await expect(page.getByRole('heading', { name: '本次研究是否包含海外子公司？' })).toBeVisible();
    await page.getByLabel('或补充你的实际情况').fill('海外主体名单仍在核实。');
    await page.getByRole('button', { name: '保存说明，暂不继续' }).click();

    await expect(page.getByText('补充说明已保存，任务仍保持暂停')).toBeVisible();
    await expect(page.getByRole('heading', { name: '本次研究是否包含海外子公司？' })).toBeVisible();
    expect(submitted[0]).toMatchObject({
      answer: '海外主体名单仍在核实。',
      selected_option: null,
      use_recommended_option: false,
      finalize: false,
      expected_control_version: 3,
    });

    await page.getByRole('button', { name: '按推荐假设继续：仅境内主体' }).click();
    await expect(page.getByRole('heading', { name: '本次研究是否包含海外子公司？' })).toHaveCount(0);
    expect(submitted[1]).toMatchObject({
      answer: null,
      selected_option: null,
      use_recommended_option: true,
      finalize: true,
      expected_control_version: 3,
    });
  });

  test('restores follow-up progress and run-scoped Evidence after a page reload', async ({ page }) => {
    await mockCompletedOpportunityTask(page, G5_TASK_ID, 'G5');
    let draftCreated = false;
    const summary = {
      research_run_id: FOLLOW_UP_RUN_ID,
      task_id: FOLLOW_UP_TASK_ID,
      task_run_id: '5d000000-0000-0000-0000-000000000001',
      run_type: 'FOLLOW_UP',
      status: 'COMPLETED',
      question: '核验合同到期时间',
      search_query_count: 2,
      search_result_count: 8,
      fetched_result_count: 3,
      evidence_count: 1,
      evidence_by_domain: { external: 1, customer_private: 0, internal: 0 },
      evidence_items: [{
        id: '5e000000-0000-0000-0000-000000000001',
        dimension: 'contract_lifecycle',
        title: '现有系统运维合同公告',
        snippet: '公告显示服务期截至 2027 年 3 月。',
        url: 'https://example.test/contract',
        source_type: 'government',
        data_domain: 'external',
        published_at: '2026-06-01T00:00:00Z',
        captured_at: '2026-07-22T00:05:00Z',
      }],
      started_at: '2026-07-22T00:00:00Z',
      ended_at: '2026-07-22T00:05:00Z',
      created_at: '2026-07-22T00:00:00Z',
    };
    await page.unroute(`**/api/report-threads/${THREAD_ID}/follow-ups`);
    await page.route(`**/api/report-threads/${THREAD_ID}/follow-ups`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [summary] }) });
    });
    await page.route(`**/api/research-runs/${FOLLOW_UP_RUN_ID}/summary`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(summary) });
    });
    await page.route(`**/api/tasks/${FOLLOW_UP_TASK_ID}/execution`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          task_id: FOLLOW_UP_TASK_ID,
          desired_state: 'RUNNING',
          observed_state: 'COMPLETED',
          control_version: 1,
          active_run: null,
          dimensions: [{ dimension: 'contract_lifecycle', total_units: 4, completed_units: 4, remaining_units: 0, status_counts: { COMPLETED: 4 } }],
          remaining_work_units: 0,
          budget: { reserved_amount: 0, settled_amount: 0, refunded_amount: 0, net_reserved_amount: 0, currencies: [] },
          latest_heartbeat_at: '2026-07-22T00:05:00Z',
          latest_checkpoint: null,
          recovery_count: 0,
          eta: null,
        }),
      });
    });
    const followUpDraft = {
      id: '5f000000-0000-0000-0000-000000000001',
      report_id: REPORT_ID,
      base_version_id: VERSION_ID,
      thread_id: THREAD_ID,
      research_run_id: FOLLOW_UP_RUN_ID,
      proposed_content_md: '## 补充研究：核验合同到期时间\n\n合同于 2027 年 3 月到期。',
      proposed_raw_data: { follow_up_runs: [{ research_run_id: FOLLOW_UP_RUN_ID }] },
      proposed_evidence_index: {
        dimensions: { contract_lifecycle: [{ id: summary.evidence_items[0].id }] },
        follow_up_runs: [{ research_run_id: FOLLOW_UP_RUN_ID }],
      },
      summary: '合并补充研究的正文、数据与 Evidence',
      change_set: [{
        id: 'change-1', kind: 'INSERT', base_start: 2, base_end: 2,
        before: '', after: '## 补充研究：核验合同到期时间\n\n合同于 2027 年 3 月到期。',
      }],
      decision: {},
      status: 'DRAFT',
      idempotency_key: 'follow-up-draft',
      accepted_version_id: null,
      created_at: '2026-07-22T00:06:00Z',
    };
    await page.unroute(`**/api/reports/${REPORT_ID}/drafts`);
    await page.route(`**/api/reports/${REPORT_ID}/drafts`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: draftCreated ? [followUpDraft] : [] }),
      });
    });
    await page.route(`**/api/research-runs/${FOLLOW_UP_RUN_ID}/report-draft`, async (route) => {
      draftCreated = true;
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(followUpDraft) });
    });

    await page.goto(`/tasks/${G5_TASK_ID}`);
    await page.getByRole('button', { name: '分析报告' }).click();
    await expect(page.getByTestId('follow-up-research-status')).toBeVisible();
    await expect(page.getByText('现有系统运维合同公告')).toBeVisible();
    await expect(page.getByText('正式 Evidence 1')).toBeVisible();
    await expect(page.getByText(/不会自动覆盖原报告/)).toBeVisible();
    await page.getByRole('button', { name: '生成修订草案' }).click();
    await expect(page.getByText('报告修订草案与 Diff')).toBeVisible();
    await expect(page.getByText(/只能整体接受或拒绝/)).toBeVisible();
    await expect(page.getByRole('button', { name: /接受所选/ })).toHaveCount(0);

    await page.reload();
    await page.getByRole('button', { name: '分析报告' }).click();
    await expect(page.getByTestId('follow-up-research-status')).toBeVisible();
    await expect(page.getByText('现有系统运维合同公告')).toBeVisible();
  });
});
