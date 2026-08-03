import { expect, test } from '@playwright/test';
import { mockNotificationRoutes } from './mocks/setup';


const ACCOUNT_ID = '61000000-0000-0000-0000-000000000001';
const TASK_ID = '62000000-0000-0000-0000-000000000001';
const REPORT_ID = '63000000-0000-0000-0000-000000000001';
const THREAD_ID = '6b000000-0000-0000-0000-000000000001';
const CLAIM_ID = '67000000-0000-0000-0000-000000000001';
const HYPOTHESIS_ID = '69000000-0000-0000-0000-000000000001';
const FRAMEWORK_ID = '6e000000-0000-0000-0000-000000000001';
const OPPORTUNITY_ID = '6f000000-0000-0000-0000-000000000001';


test('customer workbench connects research claims hypotheses products and actions', async ({ page }) => {
  await mockNotificationRoutes(page);
  let hypothesisStatus = 'PENDING_SALES_REVIEW';
  let claimStatus = 'SUPPORTED';
  let actionStatus = 'PENDING';
  let actionDueAt: string | null = null;
  let latestQualification: Record<string, unknown> | null = null;
  let opportunityStage = 'QUALIFICATION';
  let formalOpportunities: Array<Record<string, unknown>> = [];
  await page.route(`**/api/target-accounts/${ACCOUNT_ID}/workbench`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        account: {
          id: ACCOUNT_ID,
          input_name: '未来银行',
          official_name: '未来银行股份有限公司',
          website: 'https://example.com',
          credit_code: null,
          industry: '银行',
          region: '上海',
          stock_code: null,
          parent_id: null,
          status: 'CONFIRMED',
          created_at: '2026-07-22T00:00:00Z',
          updated_at: '2026-07-22T00:00:00Z',
        },
        counts: { tasks: 1, claims: 1, gate_decisions: 1, hypotheses: 1, opportunities: formalOpportunities.length, pending_actions: 1 },
        tasks: [{
          id: TASK_ID,
          demand_direction: '数据治理升级',
          status: 'COMPLETED',
          observed_state: 'COMPLETED',
          research_mode: 'OPPORTUNITY_DISCOVERY',
          created_at: '2026-07-22T00:00:00Z',
          updated_at: '2026-07-22T00:10:00Z',
          report_id: REPORT_ID,
          report_version_id: '64000000-0000-0000-0000-000000000001',
          report_version_no: 1,
          latest_product_match: {
            id: '65000000-0000-0000-0000-000000000001',
            status: 'MATCHED',
            analysis_as_of_date: '2026-07-22T00:00:00Z',
            recommendation_score: 82,
            evidence_confidence: 0.78,
            information_completeness: 0.74,
            missing_gate_layers: [],
            revalidation_conditions: [],
            matched_product_ids: ['66000000-0000-0000-0000-000000000001'],
            capability_gaps: [],
            pending_verifications: [],
            created_at: '2026-07-22T00:10:00Z',
          },
        }],
        claims: [{
          id: CLAIM_ID,
          task_id: TASK_ID,
          report_version_id: '64000000-0000-0000-0000-000000000001',
          claim_text: '客户正在验证数据治理建设路径',
          claim_type: 'INFERENCE',
          opportunity_effect: 'positive',
          status: claimStatus,
          confidence: 0.82,
          evidence_count: 2,
          last_verified_at: '2026-07-22T00:08:00Z',
          expires_at: null,
          updated_at: '2026-07-22T00:08:00Z',
        }],
        latest_gate: {
          id: '68000000-0000-0000-0000-000000000001',
          task_id: TASK_ID,
          decision: 'POTENTIAL_WINDOW',
          gate_level: 'G4',
          analysis_as_of_date: '2026-07-22T00:00:00Z',
          summary: { reasons: ['客户需求与采购窗口已有支持证据'] },
          created_at: '2026-07-22T00:09:00Z',
        },
        hypotheses: [{
          id: HYPOTHESIS_ID,
          source_task_id: TASK_ID,
          gate_decision_id: '68000000-0000-0000-0000-000000000001',
          title: '数据治理商机假设',
          customer_problem_hypothesis: '数据标准尚未统一',
          business_impact_hypothesis: '跨部门协作成本较高',
          trigger_event: '公开建设规划进入验证期',
          counter_evidence_summary: '',
          hard_blockers: [],
          status: hypothesisStatus,
          confidence: 0.76,
          information_completeness: 0.68,
          owner_user_id: null,
          expires_at: '2026-10-20T00:00:00Z',
          supporting_claim_ids: [CLAIM_ID],
          refuting_claim_ids: [],
          latest_qualification: latestQualification,
          candidate_products: [{
            product_id: '66000000-0000-0000-0000-000000000001',
            name: '智能服务研究平台',
            version_label: '1.0',
            fit_score: 0.82,
            rationale: '需求与能力边界相符',
          }],
          actions: [{
            id: '6a000000-0000-0000-0000-000000000001',
            objective: '确认数据治理项目的责任部门与时间窗口',
            target_role: '数据管理负责人',
            recommended_channel: '会议',
            talking_point: '',
            suggested_questions: [],
            expected_outcome: '获得一次需求访谈',
            owner_user_id: null,
            due_at: actionDueAt,
            status: actionStatus,
            result: null,
            created_at: '2026-07-22T00:10:00Z',
            updated_at: '2026-07-22T00:10:00Z',
          }],
          created_at: '2026-07-22T00:10:00Z',
          updated_at: '2026-07-22T00:10:00Z',
        }],
        opportunities: formalOpportunities,
      }),
    });
  });
  await page.route(`**/api/opportunities/hypotheses/${HYPOTHESIS_ID}/decisions`, async (route) => {
    const payload = route.request().postDataJSON();
    if (payload.decision === 'ACCEPT') hypothesisStatus = 'SALES_ACCEPTED';
    if (payload.decision === 'CONFIRM_CUSTOMER') {
      hypothesisStatus = 'CUSTOMER_VALIDATED';
      claimStatus = 'CUSTOMER_CONFIRMED';
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        hypothesis_id: '69000000-0000-0000-0000-000000000001',
        status: hypothesisStatus,
        owner_user_id: '60000000-0000-0000-0000-000000000001',
        deferred_until: null,
        expires_at: '2026-10-20T00:00:00Z',
        transition: {
          id: '6c000000-0000-0000-0000-000000000001',
          from_status: 'PENDING_SALES_REVIEW',
          to_status: hypothesisStatus,
          reason: payload.reason,
          request_key: payload.request_key,
          changed_by: '60000000-0000-0000-0000-000000000001',
          created_at: '2026-07-22T00:20:00Z',
        },
        created: true,
      }),
    });
  });
  await page.route('**/api/opportunities/qualification-frameworks', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [{
        id: FRAMEWORK_ID,
        workspace_id: '60000000-0000-0000-0000-000000000010',
        framework_key: 'ENTERPRISE_DEFAULT',
        version_no: 1,
        name: '企业级商机资格标准',
        methodology: 'HYBRID',
        criteria: [{ key: 'problem', label: '客户问题', weight: 1, required: true }],
        hard_blocker_rules: [],
        minimum_score: 0.7,
        minimum_completeness: 1,
        status: 'PUBLISHED',
        created_by: '60000000-0000-0000-0000-000000000001',
        published_at: '2026-07-22T00:00:00Z',
        created_at: '2026-07-22T00:00:00Z',
      }] }),
    });
  });
  await page.route(`**/api/opportunities/target-accounts/${ACCOUNT_ID}/stakeholders`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
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
  await page.route(`**/api/opportunities/hypotheses/${HYPOTHESIS_ID}/qualification-assessments`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: latestQualification ? [latestQualification] : [] }) });
      return;
    }
    latestQualification = {
      id: '6e100000-0000-0000-0000-000000000001',
      workspace_id: '60000000-0000-0000-0000-000000000010',
      hypothesis_id: HYPOTHESIS_ID,
      framework_id: FRAMEWORK_ID,
      assessment_no: 1,
      framework_key: 'ENTERPRISE_DEFAULT',
      framework_version: '1',
      criteria: [],
      hard_blockers: [],
      missing_fields: [],
      gate_result: 'PASS',
      score: 1,
      information_completeness: 1,
      summary: '资格结果 PASS；客户问题已确认。',
      assessed_by: '60000000-0000-0000-0000-000000000001',
      assessed_at: '2026-07-22T00:40:00Z',
      created_at: '2026-07-22T00:40:00Z',
    };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ card: latestQualification, created: true }) });
  });
  await page.route(`**/api/opportunities/hypotheses/${HYPOTHESIS_ID}/convert`, async (route) => {
    const payload = route.request().postDataJSON();
    hypothesisStatus = 'CONVERTED';
    formalOpportunities = [{
      id: OPPORTUNITY_ID,
      source_hypothesis_id: HYPOTHESIS_ID,
      title: payload.title,
      stage: opportunityStage,
      owner_user_id: '60000000-0000-0000-0000-000000000001',
      amount: payload.amount ?? null,
      currency: payload.currency ?? null,
      amount_source: payload.amount_source ?? 'UNSPECIFIED',
      probability: payload.probability,
      expected_close_date: payload.expected_close_date ?? null,
      closed_at: null,
      close_reason: null,
      created_at: '2026-07-22T00:50:00Z',
      updated_at: '2026-07-22T00:50:00Z',
    }];
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      opportunity: { ...formalOpportunities[0], workspace_id: '60000000-0000-0000-0000-000000000010', target_account_id: ACCOUNT_ID },
      transition: { id: '6f100000-0000-0000-0000-000000000001', from_stage: null, to_stage: 'QUALIFICATION', reason: payload.reason, request_key: payload.request_key, changed_by: '60000000-0000-0000-0000-000000000001', created_at: '2026-07-22T00:50:00Z' },
      created: true,
    }) });
  });
  await page.route(`**/api/opportunities/${OPPORTUNITY_ID}/stages`, async (route) => {
    const payload = route.request().postDataJSON();
    const fromStage = opportunityStage;
    opportunityStage = payload.to_stage;
    formalOpportunities[0] = { ...formalOpportunities[0], stage: opportunityStage, updated_at: '2026-07-22T01:00:00Z' };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      opportunity: { ...formalOpportunities[0], workspace_id: '60000000-0000-0000-0000-000000000010', target_account_id: ACCOUNT_ID },
      transition: { id: '6f200000-0000-0000-0000-000000000001', from_stage: fromStage, to_stage: opportunityStage, reason: payload.reason, request_key: payload.request_key, changed_by: '60000000-0000-0000-0000-000000000001', created_at: '2026-07-22T01:00:00Z' },
      created: true,
    }) });
  });
  await page.route(`**/api/opportunities/actions/6a000000-0000-0000-0000-000000000001/commands`, async (route) => {
    const payload = route.request().postDataJSON();
    actionStatus = payload.command === 'START' ? 'IN_PROGRESS' : actionStatus;
    actionDueAt = payload.due_at ?? actionDueAt;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        action_id: '6a000000-0000-0000-0000-000000000001',
        status: actionStatus,
        owner_user_id: '60000000-0000-0000-0000-000000000001',
        due_at: actionDueAt,
        result: null,
        transition: {
          id: '6d000000-0000-0000-0000-000000000001',
          from_status: 'PENDING',
          to_status: actionStatus,
          reason: payload.reason,
          result: null,
          request_key: payload.request_key,
          changed_by: '60000000-0000-0000-0000-000000000001',
          created_at: '2026-07-22T00:30:00Z',
        },
        created: true,
      }),
    });
  });
  await page.route(`**/api/reports/${REPORT_ID}/threads`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [{
        id: THREAD_ID,
        report_id: REPORT_ID,
        bound_version_id: '64000000-0000-0000-0000-000000000001',
        title: '报告深度讨论',
        status: 'ACTIVE',
        created_at: '2026-07-22T00:10:00Z',
        updated_at: '2026-07-22T00:10:00Z',
      }] }),
    });
  });
  await page.route(`**/api/report-threads/${THREAD_ID}/messages`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route(`**/api/report-threads/${THREAD_ID}/follow-ups`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });
  await page.route(`**/api/reports/${REPORT_ID}/drafts`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
  });

  await page.goto(`/customers/${ACCOUNT_ID}`);

  await expect(page.getByTestId('customer-workbench')).toBeVisible();
  await expect(page.getByRole('heading', { name: '未来银行股份有限公司' })).toBeVisible();
  await expect(page.getByText('数据治理商机假设')).toBeVisible();
  await expect(page.getByText(/智能服务研究平台 1\.0/)).toBeVisible();
  await expect(page.getByText('确认数据治理项目的责任部门与时间窗口')).toBeVisible();
  await expect(page.getByText('客户正在验证数据治理建设路径')).toBeVisible();
  await expect(page.getByText('正式报告 V1')).toBeVisible();
  await expect(page.getByText('G4')).toBeVisible();
  await expect(page.getByRole('heading', { name: '继续与报告智能体探讨' })).toBeVisible();

  await page.getByRole('button', { name: '接受并安排验证' }).click();
  await page.getByLabel('裁决原因').fill('销售确认值得进入客户验证');
  await page.getByLabel('行动截止日期').fill('2026-08-01');
  await page.getByRole('button', { name: '确认裁决' }).click();
  await expect(page.getByText('销售已接受')).toBeVisible();

  await page.getByRole('button', { name: '开始执行' }).click();
  await page.getByLabel('变更原因').fill('开始联系客户负责人');
  await page.getByLabel('截止日期').fill('2026-08-01');
  await page.getByRole('button', { name: '确认更新' }).click();
  await expect(page.getByText('进行中')).toBeVisible();

  await page.getByRole('button', { name: '客户已确认（需确认 Claim）' }).click();
  await page.getByLabel('裁决原因').fill('客户会议已经确认问题与优先级');
  await page.getByRole('button', { name: '确认裁决' }).click();
  await expect(page.getByText('客户已确认', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: '开始评估' }).click();
  await page.getByLabel('判断状态').selectOption('CUSTOMER_CONFIRMED');
  await page.getByLabel('引用 Claim（可多选）').selectOption(CLAIM_ID);
  await page.getByRole('button', { name: '生成资格卡' }).click();
  await expect(page.getByText(/资格结果 PASS/)).toBeVisible();

  await page.getByRole('button', { name: '创建正式商机' }).click();
  await page.getByLabel('创建依据').fill('G5、客户确认与资格卡均已通过');
  await page.getByRole('button', { name: '确认创建' }).click();
  await expect(page.getByTestId('formal-opportunity')).toBeVisible();

  await page.getByLabel('下一阶段').selectOption('DISCOVERY');
  await page.getByLabel('推进依据').fill('已完成资格确认，进入需求发现');
  await page.getByRole('button', { name: '确认推进' }).click();
  await expect(page.getByText('需求发现', { exact: true })).toBeVisible();
});
