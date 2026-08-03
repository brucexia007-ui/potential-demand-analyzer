/**
 * v3.1 E2E Mock 路由注册工具
 *
 * 每个 spec 在 test.beforeEach 中调用对应的 mock*Routes 函数，
 * 用 page.route() 拦截 API 请求返回固定 JSON，避免依赖 LLM/Search 外部服务。
 *
 * 使用方式：
 *   import { mockAdvisorRoutes, mockSkillsRoutes } from '../mocks/setup';
 *   test.beforeEach(async ({ page }) => {
 *     await mockAdvisorRoutes(page);
 *     await mockSkillsRoutes(page);
 *   });
 */

import type { Page } from '@playwright/test';

// ── Mock 数据工厂 ──────────────────────────────────────────────

export function mockUUID(prefix = '00000000'): string {
  const hex = () => Math.floor(Math.random() * 65536).toString(16).padStart(4, '0');
  return `${prefix}-${hex()}${hex()}-${hex()}-${hex()}-${hex()}${hex()}${hex()}`;
}

// ── Authentication ───────────────────────────────────────────────

export async function mockAuthRoutes(page: Page, initiallyAuthenticated = true) {
  let authenticated = initiallyAuthenticated;
  const user = { id: 'e2e-user', username: 'admin', is_active: true };

  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill(authenticated
      ? {
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(user),
        }
      : { status: 401 });
  });
  await page.route('**/api/auth/login', async (route) => {
    authenticated = true;
    await page.context().addCookies([{
      name: 'kanyikan_access',
      value: 'e2e-access-cookie',
      url: new URL(route.request().url()).origin,
      httpOnly: true,
      sameSite: 'Lax',
    }]);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        username: user.username,
        access_expires_in_seconds: 1800,
        session_expires_in_seconds: 604800,
      }),
    });
  });
  await page.route('**/api/auth/refresh', async (route) => {
    await route.fulfill({ status: authenticated ? 200 : 401 });
  });
}

// ── Config / Status ────────────────────────────────────────────

export async function mockConfigStatus(page: Page, configured: boolean, setupCompleted = configured) {
  await page.route('**/api/config/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        setup_completed: setupCompleted,
        setup_mode: configured ? 'READY' : setupCompleted ? 'BROWSE_ONLY' : null,
        execution_ready: configured,
        llm: { configured, verification_status: configured ? 'PASSED' : 'UNTESTED', ready: configured, last_tested_at: null, error_code: null, error_message: null, provider_count: configured ? 1 : 0, configured_provider_count: configured ? 1 : 0 },
        search: { configured, verification_status: configured ? 'PASSED' : 'UNTESTED', ready: configured, last_tested_at: null, error_code: null, error_message: null, provider_count: configured ? 1 : 0, configured_provider_count: configured ? 1 : 0 },
        model_routes_ready: configured,
        blocking_items: configured ? [] : [
          { capability: 'llm', status: 'UNTESTED', action: '/settings/providers' },
          { capability: 'search', status: 'UNTESTED', action: '/settings/search' },
        ],
        warnings: [],
      }),
    });
  });
}

// ── Advisor (SmartTaskForm) ─────────────────────────────────────

export async function mockAdvisorRoutes(page: Page) {
  // interpret
  await page.route('**/api/advisor/interpret', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        company_name: '某市政务服务中心',
        demand_direction: '智能客服系统升级改造',
        industry: '政务',
        region: '华东',
        business_goal: '提升政务服务效率和民众满意度',
        suggested_skill: 'customer_service',
        confidence: 0.85,
        missing_fields: [],
        raw_llm_output: 'mock interpret result',
      }),
    });
  });

  // plan
  await page.route('**/api/advisor/plan', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suggested_dimensions: [
          'bidding_information',
          'policy_compliance',
          'service_capability',
          'official_pr',
        ],
        suggested_depth: 'standard',
        suggested_focus: ['智能客服', '政务信息化', '数字化转型'],
        suggested_complexity: 'medium',
        estimated_iterations: 3,
        reasoning: 'Mock 规划推理：基于政务行业匹配 4 个维度...',
        raw_llm_output: 'mock plan result',
      }),
    });
  });

  // create-task
  await page.route('**/api/advisor/create-task', async (route) => {
    const taskId = mockUUID('aaaa');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId,
        brief_id: mockUUID('bbbb'),
        status: 'PENDING',
        execution_mode: 'harness',
      }),
    });
  });
}

// ── Skills ──────────────────────────────────────────────────────

export async function mockSkillsRoutes(page: Page) {
  await page.route('**/api/skills/runtime', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        skills: [
          {
            name: 'pilot-opportunity',
            description: '基于证据、生命周期与 OIG 的售前商机研究一级 Skill',
            version: 1,
            execution_order: [
              'resolving-target-company',
              'researching-bidding-history',
              'analyzing-policy-drivers',
              'mining-customer-pain-points',
              'matching-product-capabilities',
              'pilot-opportunity',
            ],
            research_skills: [
              'resolving-target-company',
              'researching-bidding-history',
              'analyzing-policy-drivers',
              'mining-customer-pain-points',
            ],
            evaluation_skills: ['matching-product-capabilities'],
          },
        ],
        total: 1,
      }),
    });
  });

  const skillsData = [
    {
      id: '10000000-0000-0000-0000-000000000001',
      name: 'pilot-opportunity',
      display_name: '标准商机研究',
      description: '系统内置的售前商机研究一级 Skill',
      scope: 'SYSTEM',
      status: 'PUBLISHED',
      editable: false,
      current_version_id: '20000000-0000-0000-0000-000000000001',
      latest_version: {
        id: '20000000-0000-0000-0000-000000000001', version: 1,
        status: 'PUBLISHED', content_hash: 'a'.repeat(64),
        compiled_spec: {
          name: 'pilot-opportunity', description: '系统内置的售前商机研究一级 Skill', license: null, version: 1,
          execution_phase: 'research', allowed_tools: ['external_search'], data_domains: ['external'],
          questions: ['客户为什么现在需要行动'], sources: ['客户官网'], dependencies: ['matching-product-capabilities@1'],
          report_sections: ['关键发现'], dependency_conditions: {}, budget: {}, stop_conditions: [], output_fields: [], quality_thresholds: {}, triggers: [],
        },
        compiled_at: '2026-07-22T00:00:00Z', published_at: '2026-07-22T00:00:00Z', created_at: '2026-07-22T00:00:00Z',
      },
      created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
    },
    {
      id: '10000000-0000-0000-0000-000000000002',
      name: 'industry-account-research',
      display_name: '行业客户研究',
      description: 'Workspace 自定义客户研究策略',
      scope: 'WORKSPACE',
      status: 'PUBLISHED',
      editable: true,
      current_version_id: '20000000-0000-0000-0000-000000000002',
      latest_version: {
        id: '20000000-0000-0000-0000-000000000003', version: 2,
        status: 'COMPILED', content_hash: 'b'.repeat(64),
        compiled_spec: {
          name: 'industry-account-research', description: 'Workspace 自定义客户研究策略', license: null, version: 2,
          execution_phase: 'research', allowed_tools: [], data_domains: [], dependency_conditions: {},
          questions: ['客户为什么现在需要行动'], sources: ['客户官网'], report_sections: ['关键发现'],
          dependencies: [], budget: {}, stop_conditions: [], output_fields: [], quality_thresholds: {}, triggers: [],
        },
        compiled_at: '2026-07-22T00:00:00Z', published_at: null, created_at: '2026-07-22T00:00:00Z',
      },
      created_at: '2026-07-22T00:00:00Z', updated_at: '2026-07-22T00:00:00Z',
    },
  ];
  const evalCases: Record<string, unknown>[] = [];
  let evalRuns: Record<string, unknown>[] = [];

  // list
  await page.route('**/api/skills?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ skills: skillsData, total: skillsData.length }),
    });
  });

  // single detail
  await page.route(/\/api\/skills\/[^/?]+$/, async (route) => {
    const url = route.request().url();
    const skillId = url.split('/').pop()?.split('?')[0];
    const skill = skillsData.find((s) => s.id === skillId) || skillsData[0];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...skill,
        versions: [skill.latest_version],
      }),
    });
  });

  // create
  await page.route('**/api/skills', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          skill: skillsData[1],
          version: skillsData[1].latest_version,
        }),
      });
    } else {
      await route.continue();
    }
  });

  await page.route(/\/api\/skills\/[^/]+\/versions\/[^/]+\/source$/, async (route) => {
    const isSystem = route.request().url().includes(skillsData[0].id);
    const markdown = isSystem
      ? '---\nname: pilot-opportunity\ndescription: 系统内置的售前商机研究一级 Skill\nmetadata:\n  version: "1"\n  execution_phase: research\n  allowed_tools: [external_search]\n  data_domains: [external]\n---\n## Questions\n- 客户为什么现在需要行动\n## Sources\n- 客户官网\n## Dependencies\n- matching-product-capabilities@1\n'
      : '---\nname: industry-account-research\ndescription: Workspace 自定义客户研究策略\nmetadata:\n  version: "2"\n---\n## Questions\n- 客户为什么现在需要行动\n## Sources\n- 客户官网\n';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        skill_id: skillsData[1].id,
        version_id: skillsData[1].latest_version.id,
        markdown,
      }),
    });
  });

  await page.route('**/api/skills/compile-preview', async (route) => {
    const source = route.request().postDataJSON().source as string;
    const system = source.includes('name: pilot-opportunity');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ valid: true, compiled_spec: system ? skillsData[0].latest_version.compiled_spec : skillsData[1].latest_version.compiled_spec, errors: [], warnings: [] }),
    });
  });

  await page.route(/\/api\/skills\/[^/]+\/versions\/[^/]+\/dry-run$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tool_plan: ['SEARCH: 客户官网'], budget: {}, external_execution: false }),
    });
  });

  await page.route(/\/api\/skills\/[^/]+\/eval-cases$/, async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      const value = {
        id: '30000000-0000-0000-0000-000000000001',
        skill_id: skillsData[1].id,
        ...body,
        enabled: true,
        created_at: '2026-07-22T00:00:00Z',
      };
      evalCases.push(value);
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(value) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(evalCases) });
  });

  await page.route(/\/api\/skills\/[^/]+\/versions\/[^/]+\/eval-runs$/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(evalRuns) });
  });

  await page.route(/\/api\/skills\/[^/]+\/versions\/[^/]+\/evaluate$/, async (route) => {
    evalRuns = evalCases.map((item, index) => ({
      id: `40000000-0000-0000-0000-${String(index + 1).padStart(12, '0')}`,
      version_id: skillsData[1].latest_version.id,
      case_id: item.id,
      status: 'PASSED',
      metrics: { evidence_count: 3, citation_coverage: 1, manual_score: 90 },
      result: { evaluator: 'deterministic-v1', checks: { trigger: true }, failures: [], external_execution: false },
      model: null,
      initiated_by: null,
      started_at: '2026-07-22T00:00:00Z',
      finished_at: '2026-07-22T00:00:01Z',
      created_at: '2026-07-22T00:00:00Z',
    }));
    skillsData[1].latest_version.status = 'EVALUATED';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ passed: true, version_status: 'EVALUATED', runs: evalRuns }),
    });
  });

  await page.route(/\/api\/skills\/[^/]+\/versions\/[^/]+\/publish$/, async (route) => {
    skillsData[1].current_version_id = skillsData[1].latest_version.id;
    skillsData[1].latest_version.status = 'PUBLISHED';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ skill: { ...skillsData[1], current_version_id: skillsData[1].latest_version.id }, version: { ...skillsData[1].latest_version, status: 'PUBLISHED' } }),
    });
  });

  await page.route(/\/api\/skills\/[^/]+\/versions$/, async (route) => {
    await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ skill: skillsData[1], version: skillsData[1].latest_version }) });
  });

  await page.route(/\/api\/skills\/[^/]+\/archive$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ skill: { ...skillsData[1], status: 'ARCHIVED' }, version: null }),
    });
  });
}

// ── Settings Config ─────────────────────────────────────────────

export async function mockSettingsRoutes(page: Page) {
  const defaultConfig = {};

  const configEndpoints = ['budget', 'crawler', 'data-retention', 'security'];

  for (const section of configEndpoints) {
    await page.route(`**/api/config/${section}`, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(defaultConfig),
        });
      } else if (route.request().method() === 'PUT') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'ok', message: `${section} 配置已保存` }),
        });
      } else {
        await route.continue();
      }
    });
  }

  // export
  await page.route('**/api/config/export', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        budget: {},
        crawler: {},
        data_retention: {},
        security: {},
      }),
    });
  });

  // import
  await page.route('**/api/config/import', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', imported: ['budget'] }),
      });
    } else {
      await route.continue();
    }
  });
}

// ── Setup Wizard ────────────────────────────────────────────────

export async function mockSetupTestRoutes(page: Page) {
  // LLM provider create
  await page.route('**/api/config/providers', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 1,
          name: 'Mock Provider',
          provider_type: 'openai',
          base_url: 'https://api.mock.example.com/v1',
          enabled: true,
        }),
      });
    } else {
      await route.continue();
    }
  });

  // LLM test
  await page.route(/\/api\/config\/providers\/\d+\/test$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        models: ['mock-gpt-4', 'mock-gpt-3.5'],
        latency_ms: 150,
        message: '连接成功',
      }),
    });
  });

  // Search provider create
  await page.route('**/api/config/search', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 1,
          name: 'Mock Search',
          provider_type: 'bocha',
          enabled: true,
        }),
      });
    } else {
      await route.continue();
    }
  });

  // Search test
  await page.route(/\/api\/config\/search\/\d+\/test$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        result_count: 5,
        latency_ms: 300,
        message: '搜索测试成功',
      }),
    });
  });

  // model routes
  await page.route('**/api/config/model-routes', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.route('**/api/config/model-routes-preset', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        preset: 'balanced',
        route_count: 3,
        selected_model: 'mock-gpt-4',
      }),
    });
  });
}

// ── Batch Import ────────────────────────────────────────────────

export async function mockBatchImportRoutes(page: Page) {
  // preview
  await page.route('**/api/batches/import/preview', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        filename: 'test.csv',
        template_id: 'standard_research',
        template_version: 1,
        source_row_count: 3,
        headers: ['企业名称', '需求方向', '行业', '地区'],
        field_mapping: [
          { standard_field: 'company_name', detected_header: '企业名称', confidence: 'high' },
          { standard_field: 'demand_direction', detected_header: '需求方向', confidence: 'high' },
          { standard_field: 'industry', detected_header: '行业', confidence: 'high' },
          { standard_field: 'region', detected_header: '地区', confidence: 'high' },
        ],
        preview_candidates: [
          { source_row_index: 2, company_name: '测试企业A', demand_direction: '智能客服升级', industry: '政务', region: '北京' },
          { source_row_index: 3, company_name: '测试企业B', demand_direction: '数据中心建设', industry: '金融', region: '上海' },
          { source_row_index: 4, company_name: '测试企业C', demand_direction: '网络安全加固', industry: '教育', region: '广州' },
        ],
        candidate_rows: [
          { source_row_index: 2, company_name: '测试企业A', demand_direction: '智能客服升级', industry: '政务', region: '北京' },
          { source_row_index: 3, company_name: '测试企业B', demand_direction: '数据中心建设', industry: '金融', region: '上海' },
          { source_row_index: 4, company_name: '测试企业C', demand_direction: '网络安全加固', industry: '教育', region: '广州' },
        ],
        warnings: [],
      }),
    });
  });

  // validate
  await page.route('**/api/batches/import/validate', async (route) => {
    const candidates = (route.request().postDataJSON().candidate_rows || []) as Array<Record<string, unknown>>;
    const rows = candidates.map((candidate) => {
      const company = String(candidate.company_name || '').trim();
      const demand = String(candidate.demand_direction || '').trim();
      const valid = Boolean(company && demand);
      return {
        source_row_index: candidate.source_row_index,
        validation_status: valid ? 'valid' : 'error',
        sample_score: valid ? 0.9 : 0,
        error_code: valid ? null : 'REQUIRED_FIELD_MISSING',
        error_message: valid ? null : '企业名称或需求方向为空',
        normalized_row: valid ? {
          company_name: company,
          demand_direction: demand,
          industry: candidate.industry || null,
          region: candidate.region || null,
        } : null,
      };
    });
    const validCount = rows.filter((row) => row.validation_status === 'valid').length;
    const errorCount = rows.length - validCount;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total_rows: rows.length,
        valid_count: validCount,
        warning_count: 0,
        error_count: errorCount,
        rows,
      }),
    });
  });

  // dry-run
  await page.route('**/api/batches/import/dry-run', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        samples: [
          { row_index: 0, company_name: '测试企业A', demand_direction: '智能客服升级', sample_score: 0.9, rank: 1 },
          { row_index: 1, company_name: '测试企业B', demand_direction: '数据中心建设', sample_score: 0.85, rank: 2 },
        ],
        cost_estimate: {
          estimated_total_tokens: 50000,
          estimated_total_time_minutes: 15,
          monetary_cost: { status: 'UNAVAILABLE', amount: null, currency: null, reason: '未配置价格' },
          total_rows: 3,
          sample_count: 2,
          confidence: 'medium',
          estimate_basis: '标准 Skill 预算',
        },
      }),
    });
  });

  // create
  await page.route('**/api/batches/import/create', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        batch_id: mockUUID('batch'),
        name: '测试批次',
        total_tasks: 3,
        status: 'RUNNING',
        import_rows_count: 3,
        accepted_rows: 3,
        rejected_rows: 0,
      }),
    });
  });

  // batch list
  await page.route('**/api/batches?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total: 0, page: 1, page_size: 20, batches: [] }),
    });
  });

  // batch detail
  await page.route(/\/api\/batches\/batch-/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        batch_id: 'batch-test',
        name: '测试批次',
        status: 'RUNNING',
        total_tasks: 3,
        completed_tasks: 1,
        failed_tasks: 0,
        cancelled_tasks: 0,
        paused: false,
      }),
    });
  });
}

// ── Task Detail ─────────────────────────────────────────────────

export async function mockTaskDetailRoutes(page: Page, taskId: string) {
  // task detail
  await page.route(new RegExp(`/api/tasks/${taskId}$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId,
        company_name: '某市政务服务中心',
        demand_direction: '智能客服系统升级',
        status: 'COMPLETED',
        created_at: '2026-07-10T10:00:00Z',
        finished_at: '2026-07-10T10:05:00Z',
        has_report: true,
      }),
    });
  });

  // durable execution projection
  await page.route(new RegExp(`/api/tasks/${taskId}/execution$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId,
        desired_state: 'RUNNING',
        observed_state: 'COMPLETED',
        control_version: 1,
        active_run: null,
        dimensions: [],
        remaining_work_units: 0,
        budget: {
          reserved_amount: 0,
          settled_amount: 0,
          refunded_amount: 0,
          net_reserved_amount: 0,
          currencies: [],
          settlement_count: 0,
          settled_token_count: 0,
        },
        latest_heartbeat_at: '2026-07-10T10:05:00Z',
        latest_checkpoint: null,
        recovery_count: 0,
        eta: null,
      }),
    });
  });

  // Research Director approved goal tree and durable task DAG
  await page.route(new RegExp(`/api/tasks/${taskId}/research-plan$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'COMPLETED',
        error_message: null,
        plan_version: 2,
        primary_goal_id: 'G0',
        goals: [
          {
            goal_id: 'G0',
            parent_id: null,
            question: '目标企业是否值得投入客服中心售前资源',
            rationale: '支持销售投入与停止决策',
            priority: 'critical',
            required: true,
            status: 'ANSWERED',
          },
          {
            goal_id: 'G1',
            parent_id: 'G0',
            question: '近期是否存在可验证的采购触发与窗口',
            rationale: '判断为什么现在介入',
            priority: 'high',
            required: true,
            status: 'ANSWERED',
          },
        ],
        tasks: [
          {
            task_id: 'T1',
            goal_ids: ['G1'],
            title: '核验目标企业采购触发',
            question: '目标企业近期是否启动客服中心采购或升级',
            rationale: '用目标企业事实验证采购动力',
            skill_name: 'researching-bidding-history',
            evidence_usage: 'TARGET_FACT',
            search_strategy: {
              target_content: ['客服中心采购公告', '中标与合同窗口'],
              preferred_sources: ['目标企业采购官网'],
              queries: ['site:example-gov.cn \"某市政务服务中心\" \"客服中心\" 招标'],
            },
            dependencies: [],
            status: 'COMPLETED',
            success_conditions: ['确认当前采购触发或完成来源覆盖'],
            stop_conditions: ['预算耗尽'],
          },
          {
            task_id: 'T2',
            goal_ids: ['G0'],
            title: '补检现有厂商与替换阻力',
            question: '现有厂商锁定是否阻碍进入',
            rationale: '形成可赢路径',
            skill_name: 'detecting-contact-center-vendor-lock-in',
            evidence_usage: 'TARGET_FACT',
            search_strategy: {
              target_content: ['现有供应商', '维保与续约'],
              preferred_sources: ['目标企业采购官网'],
              queries: ['site:example-gov.cn \"某市政务服务中心\" 客服 维保 供应商'],
            },
            dependencies: ['T1'],
            status: 'COMPLETED',
            success_conditions: ['确认在任厂商或保持未知'],
            stop_conditions: ['来源覆盖完成'],
          },
        ],
        versions: [
          {
            plan_id: '30000000-0000-0000-0000-000000000001',
            plan_version: 1,
            status: 'SUPERSEDED',
            created_at: '2026-07-10T10:00:10Z',
          },
          {
            plan_id: '30000000-0000-0000-0000-000000000002',
            plan_version: 2,
            status: 'COMPLETED',
            created_at: '2026-07-10T10:03:00Z',
          },
        ],
      }),
    });
  });

  // report
  await page.route(`**/api/reports/${taskId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId,
        content_md: `# 商机分析报告\n\n## 客户画像\n\n某市政务服务中心是市级政务服务单位。\n\n## 破冰三板斧\n\n### Why Change\n当前系统已使用5年...\n\n### Why Us\n我们的智能客服方案...\n\n### Call to Action\n建议近期安排演示...\n\n## 商机评分\n\n综合评分 87.5 分 (HIGH)。\n\n[ev:ev-001]`,
        evidence_index: {
          dimensions: {
            bidding_information: [
              { id: 'ev-001', dimension: 'bidding_information', title: '某市智能客服采购公告', snippet: '预算 200万', url: 'https://example.com/bid1', source_type: 'web_scrape', source_reliability: 'A' },
              { id: 'ev-002', dimension: 'bidding_information', title: '政务云平台招标', snippet: '项目金额 500万', url: 'https://example.com/bid2', source_type: 'web_scrape', source_reliability: 'B' },
            ],
            policy_compliance: [
              { id: 'ev-003', dimension: 'policy_compliance', title: '数字化转型三年行动计划', snippet: '鼓励智能政务...', url: 'https://example.com/policy1', source_type: 'web_scrape', source_reliability: 'S' },
            ],
          },
          validation: { passed: true, violations: [] },
          audit: {
            task_id: taskId,
            status: 'COMPLETED',
            reason_code: null,
            message: '报告结论与引用证据审计已完成。',
            audited_evidence_count: 3,
            severity: 'major',
            fatal_claims: [],
            major_claims: [
              {
                claim_id: 'claim-3',
                claim_text: '采用国产化平台是刚性要求',
                support_status: 'WEAK',
                evidence_ids: [],
                skeptic_level: 'HIGH',
                skeptic_notes: '无直接证据支持此结论',
                severity: 'major',
                replan_count: 2,
                suggested_revision: '建议与客户确认国产化要求的具体范围',
              },
            ],
            minor_claims: [
              {
                claim_id: 'claim-2',
                claim_text: '预算约 200 万',
                support_status: 'SUPPORTED',
                evidence_ids: ['ev-001'],
                skeptic_level: 'MEDIUM',
                skeptic_notes: '单一来源，可能存在偏差',
                severity: 'minor',
                replan_count: 1,
                suggested_revision: '预算约 150-200 万（单次采购）',
              },
            ],
            claim_audits: [
              {
                claim_id: 'claim-1',
                claim_text: '该单位有智能客服采购意向',
                support_status: 'SUPPORTED',
                evidence_ids: ['ev-001'],
                skeptic_level: 'LOW',
                skeptic_notes: '采购公告确认了意向',
                severity: 'acceptable',
                replan_count: 0,
              },
              {
                claim_id: 'claim-2',
                claim_text: '预算约 200 万',
                support_status: 'SUPPORTED',
                evidence_ids: ['ev-001'],
                skeptic_level: 'MEDIUM',
                skeptic_notes: '单一来源，可能存在偏差',
                severity: 'minor',
                replan_count: 1,
                suggested_revision: '预算约 150-200 万（单次采购）',
              },
              {
                claim_id: 'claim-3',
                claim_text: '采用国产化平台是刚性要求',
                support_status: 'WEAK',
                evidence_ids: [],
                skeptic_level: 'HIGH',
                skeptic_notes: '无直接证据支持此结论',
                severity: 'major',
                replan_count: 2,
                suggested_revision: '建议与客户确认国产化要求的具体范围',
              },
            ],
          },
          opportunity_score: {
            total_score: 87.5,
            grade: 'HIGH',
            dimension_scores: {
              bidding_information: { score: 90.0, weight: 0.30, evidence_count: 5 },
              policy_compliance: { score: 85.0, weight: 0.25, evidence_count: 3 },
              service_capability: { score: 88.0, weight: 0.20, evidence_count: 4 },
              official_pr: { score: 80.0, weight: 0.15, evidence_count: 2 },
              feedback: { score: 92.0, weight: 0.10, evidence_count: 6 },
            },
            counter_evidences: [],
            lockin_risks: [],
            penalties: { counter_evidence_penalty: 0, lockin_risk_penalty: 0, total_penalty: 0 },
          },
        },
        created_at: '2026-07-10T10:05:00Z',
      }),
    });
  });

  // logs
  await page.route(`**/api/tasks/${taskId}/logs`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        logs: [
          { step_name: 'init', level: 'INFO', message: '任务开始', created_at: '2026-07-10T10:00:00Z' },
          { step_name: 'search', level: 'INFO', message: '搜索完成，获取到 12 条结果', created_at: '2026-07-10T10:01:00Z' },
          { step_name: 'extraction', level: 'INFO', message: '信息提取完成', created_at: '2026-07-10T10:03:00Z' },
          { step_name: 'audit', level: 'WARNING', message: '1 条结论缺乏足够证据', created_at: '2026-07-10T10:04:00Z' },
          { step_name: 'done', level: 'INFO', message: '报告生成完毕', created_at: '2026-07-10T10:05:00Z' },
        ],
      }),
    });
  });

  // evidences
  await page.route(`**/api/reports/${taskId}/evidences`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId,
        total: 4,
        evidences: [
          { id: 'ev-001', dimension: 'bidding_information', title: '某市智能客服采购公告', snippet: '预算 200万', url: 'https://example.com/bid1', source_type: 'web_scrape', source_reliability: 'A' },
          { id: 'ev-002', dimension: 'bidding_information', title: '政务云平台招标', snippet: '项目金额 500万', url: 'https://example.com/bid2', source_type: 'web_scrape', source_reliability: 'B' },
          { id: 'ev-003', dimension: 'policy_compliance', title: '数字化转型三年行动计划', snippet: '鼓励智能政务...', url: 'https://example.com/policy1', source_type: 'government', source_reliability: 'S' },
        ],
      }),
    });
  });

  // field agent runs
  await page.route(`**/api/tasks/${taskId}/field-agent-runs`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: taskId,
        runs: [
          {
            id: 'far-001',
            task_id: taskId,
            agent_type: 'playwright_field',
            target_url: 'https://www.example-gov.cn',
            status: 'OK',
            started_at: '2026-07-10T10:02:00Z',
            finished_at: '2026-07-10T10:02:30Z',
            step_count: 5,
            screenshot_paths: ['screenshots/task_001/homepage.png', 'screenshots/task_001/services.png'],
            visited_urls: ['https://www.example-gov.cn', 'https://www.example-gov.cn/services'],
            observations: '官网展示了政务服务大厅和在线办事功能，有智能客服入口。',
            blocked_reason: null,
            evidence_ids: ['ev-010', 'ev-011'],
            created_at: '2026-07-10T10:02:00Z',
          },
        ],
        total: 1,
      }),
    });
  });
}

// ── Tasks List ──────────────────────────────────────────────────

export async function mockTasksListRoutes(page: Page) {
  await page.route('**/api/tasks?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 3,
        page: 1,
        page_size: 20,
        tasks: [
          {
            task_id: '00000000-0000-0000-0000-000000000001',
            company_name: '测试企业A',
            demand_direction: '智能客服升级',
            status: 'COMPLETED',
            created_at: '2026-07-10T10:00:00Z',
            has_report: true,
          },
          {
            task_id: '00000000-0000-0000-0000-000000000002',
            company_name: '测试企业B',
            demand_direction: '数据中心建设',
            status: 'RUNNING',
            created_at: '2026-07-10T09:00:00Z',
            has_report: false,
          },
          {
            task_id: '00000000-0000-0000-0000-000000000003',
            company_name: '测试企业C',
            demand_direction: '网络安全加固',
            status: 'FAILED',
            created_at: '2026-07-10T08:00:00Z',
            has_report: false,
          },
          {
            task_id: '00000000-0000-0000-0000-000000000004',
            company_name: '测试企业D',
            demand_direction: '客服中心商机分析',
            status: 'PARTIAL',
            created_at: '2026-07-10T07:00:00Z',
            has_report: true,
          },
        ],
      }),
    });
  });
}

// ── Notifications ───────────────────────────────────────────────

export async function mockNotificationRoutes(page: Page) {
  await page.route('**/api/notifications', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        notifications: [],
        unread_count: 0,
      }),
    });
  });
}
