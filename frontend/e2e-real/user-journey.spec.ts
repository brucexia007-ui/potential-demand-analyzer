import { expect, test, type Page, type TestInfo } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

const username = process.env.REAL_USERNAME || "admin";
const password = process.env.REAL_PASSWORD || "";
const artifactsDir = path.join(__dirname, "artifacts", "user-journey");
const observationsPath = path.join(artifactsDir, "observations.json");
const observations: Record<string, unknown> = {
  started_at: new Date().toISOString(),
  base_url: process.env.REAL_BASE_URL || "https://127.0.0.1:10443",
};

function saveObservations() {
  fs.mkdirSync(artifactsDir, { recursive: true });
  fs.writeFileSync(observationsPath, JSON.stringify(observations, null, 2), "utf8");
}

async function capture(page: Page, testInfo: TestInfo, name: string) {
  const file = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
}

async function loginFromUserInterface(page: Page, testInfo: TestInfo) {
  if (!password) {
    throw new Error("缺少 REAL_PASSWORD，真实 E2E 不允许回退到硬编码密码");
  }

  await page.goto("/");
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await expect(page.getByRole("heading", { name: "登录", exact: true })).toBeVisible();
  await capture(page, testInfo, "01-login");

  const demoAccountVisible = await page.getByText("admin / admin123", { exact: false }).isVisible().catch(() => false);
  observations.login = {
    demo_account_hint_visible: demoAccountVisible,
    demo_account_hint_matches_runtime_credential: username === "admin" && password === "admin123",
  };
  saveObservations();
  expect(demoAccountVisible, "登录页不应展示已经失效的演示密码").toBe(false);

  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/", { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "创建分析任务" })).toBeVisible();
}

async function ensureCandidateExecutionReady(page: Page) {
  const statusResponse = await page.request.get("/api/config/status");
  expect(statusResponse.ok()).toBeTruthy();
  let status = await statusResponse.json();
  observations.pre_setup_status = status;
  saveObservations();
  if (status.execution_ready) return status;

  const llmBaseUrl = process.env.REAL_LLM_BASE_URL || "";
  const llmApiKey = process.env.REAL_LLM_API_KEY || "";
  const llmModels = (process.env.REAL_LLM_MODELS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const defaultModel = process.env.REAL_DEFAULT_MODEL || llmModels[0] || "";
  const searchProviderType = process.env.REAL_SEARCH_PROVIDER || "bocha";
  const searchBaseUrl = process.env.REAL_SEARCH_BASE_URL || "";
  const searchApiKey = process.env.REAL_SEARCH_API_KEY || "";

  expect(llmBaseUrl && llmApiKey && defaultModel).toBeTruthy();
  expect(searchProviderType === "duckduckgo" || searchApiKey).toBeTruthy();

  const providersResponse = await page.request.get("/api/config/providers");
  expect(providersResponse.ok()).toBeTruthy();
  const providers = await providersResponse.json();
  let llmProvider = providers.find((item: { name?: string }) => item.name === "KIMI K3 候选栈验证");
  if (!llmProvider) {
    const createResponse = await page.request.post("/api/config/providers", {
      data: {
        name: "KIMI K3 候选栈验证",
        provider_type: "moonshot",
        base_url: llmBaseUrl,
        api_key: llmApiKey,
        models: llmModels,
        default_model: defaultModel,
        enabled: true,
        priority: 1,
        timeout_seconds: 120,
      },
    });
    expect(createResponse.ok()).toBeTruthy();
    llmProvider = await createResponse.json();
  }
  const llmTestResponse = await page.request.post(`/api/config/providers/${llmProvider.id}/test`);
  expect(llmTestResponse.ok()).toBeTruthy();
  const llmTest = await llmTestResponse.json();
  expect(llmTest.success, `LLM 连接校验失败：${llmTest.error_code || llmTest.error || "unknown"}`).toBe(true);

  const searchProvidersResponse = await page.request.get("/api/config/search");
  expect(searchProvidersResponse.ok()).toBeTruthy();
  const searchProviders = await searchProvidersResponse.json();
  let searchProvider = searchProviders.find(
    (item: { name?: string }) => item.name === "搜索候选栈验证",
  );
  if (!searchProvider) {
    const createResponse = await page.request.post("/api/config/search", {
      data: {
        name: "搜索候选栈验证",
        provider_type: searchProviderType,
        api_key: searchApiKey || null,
        base_url: searchBaseUrl || null,
        enabled: true,
        priority: 1,
        timeout_seconds: 30,
      },
    });
    expect(createResponse.ok()).toBeTruthy();
    searchProvider = await createResponse.json();
  }
  const searchTestResponse = await page.request.post(`/api/config/search/${searchProvider.id}/test`);
  expect(searchTestResponse.ok()).toBeTruthy();
  const searchTest = await searchTestResponse.json();
  expect(
    searchTest.success,
    `搜索连接校验失败：${searchTest.error_code || searchTest.error || "unknown"}`,
  ).toBe(true);

  const modelRoutesResponse = await page.request.put("/api/config/model-routes", {
    data: ["low", "medium", "high"].map((complexityLevel) => ({
      agent_role: "default",
      complexity_level: complexityLevel,
      model_name: defaultModel,
    })),
  });
  expect(modelRoutesResponse.ok()).toBeTruthy();

  const readyResponse = await page.request.get("/api/config/status");
  expect(readyResponse.ok()).toBeTruthy();
  status = await readyResponse.json();
  expect(status.execution_ready).toBe(true);
  observations.verified_provider_setup = {
    llm: llmTest,
    search: searchTest,
    model_routes_ready: status.model_routes_ready,
  };
  saveObservations();
  return status;
}

function observeBrowserFailures(page: Page) {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  const serverErrors: string[] = [];

  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("requestfailed", (request) => {
    failedRequests.push(`${request.method()} ${request.url()} :: ${request.failure()?.errorText || "unknown"}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 500) serverErrors.push(`${response.status()} ${response.url()}`);
  });

  return { consoleErrors, failedRequests, serverErrors };
}

test.afterAll(() => {
  observations.finished_at = new Date().toISOString();
  saveObservations();
});

test("用户主路径：登录、导航、设置和批量向导可用", async ({ page }, testInfo) => {
  test.setTimeout(5 * 60_000);
  const browserFailures = observeBrowserFailures(page);

  await loginFromUserInterface(page, testInfo);

  let configStatus = await ensureCandidateExecutionReady(page);
  if (!configStatus.setup_completed) {
    await page.goto("/setup");
    await expect(page.getByRole("button", { name: "完成配置并开始使用", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "完成配置并开始使用", exact: true }).click();
    await page.waitForURL((url) => url.pathname === "/", { timeout: 30_000 });
    await expect(page.getByRole("heading", { name: "创建分析任务" })).toBeVisible();
    const configResponse = await page.request.get("/api/config/status");
    expect(configResponse.ok()).toBeTruthy();
    configStatus = await configResponse.json();
  }
  expect(configStatus.setup_completed).toBe(true);
  expect(configStatus.execution_ready).toBe(true);

  const journeys = [
    { link: "批量任务", path: "/batches", heading: "批量任务" },
    { link: "能力中心", path: "/capabilities", heading: "企业能力中心" },
    { link: "经营看板", path: "/dashboard", heading: "商机经营仪表盘" },
    { link: "历史记录", path: "/history", heading: "历史任务" },
  ];

  for (const journey of journeys) {
    await page.getByRole("link", { name: journey.link, exact: true }).click();
    await page.waitForURL((url) => url.pathname === journey.path);
    await expect(page.getByRole("heading", { name: journey.heading, exact: true })).toBeVisible();
  }

  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("link", { name: "LLM Providers", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/settings/providers");
  await expect(page.getByRole("heading", { name: "LLM Provider 管理", exact: true })).toBeVisible();
  await capture(page, testInfo, "02-provider-settings");

  await page.getByRole("link", { name: "批量任务", exact: true }).click();
  const newBatchButton = page.getByRole("button", { name: "新建批量任务", exact: true });
  await expect(newBatchButton).toBeVisible({ timeout: 15_000 });
  await newBatchButton.click();
  await expect(page.getByRole("heading", { name: "批量导入任务", exact: true })).toBeVisible();
  await page.getByPlaceholder(/每行一个客户/).fill("上海银行,客服中心智能化与信创商机,金融,上海");
  await page.getByRole("button", { name: "解析文本", exact: true }).click();
  await expect(page.getByRole("heading", { name: "数据校验", exact: true })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "跳过采样，直接创建", exact: true }).click();
  await expect(page.getByRole("heading", { name: "确认创建批量任务", exact: true })).toBeVisible();
  await capture(page, testInfo, "03-batch-confirmation");

  observations.read_only_journey = {
    config_status: configStatus,
    batch_wizard_reached_confirmation: true,
    ...browserFailures,
  };
  saveObservations();

  expect(browserFailures.serverErrors).toEqual([]);
});

test("用户完整闭环：解析需求、研究执行、澄清、报告、导出和历史回查", async ({ page }, testInfo) => {
  test.setTimeout(25 * 60_000);
  const browserFailures = observeBrowserFailures(page);
  await loginFromUserInterface(page, testInfo);

  let taskId = process.env.REAL_RESUME_TASK_ID || "";
  if (taskId) {
    observations.resumed_task_id = taskId;
    await page.goto(`/tasks/${taskId}`);
    await expect(page.getByRole("heading", { name: "任务详情", exact: true })).toBeVisible();
  } else {
    const companyName = process.env.REAL_COMPANY_NAME || "上海银行";
    const demandDirection = process.env.REAL_DEMAND_DIRECTION || "客服中心智能化、信创改造、呼叫平台、IP电话与客服BPO商机分析";
    const industry = process.env.REAL_INDUSTRY || "银行";
    const region = process.env.REAL_REGION || "上海";
    const researchDepth = process.env.REAL_RESEARCH_DEPTH || "quick";
    const prompt = process.env.REAL_RESEARCH_PROMPT ||
      `请分析${companyName}在客服中心智能化、国产化信创改造、呼叫平台、IP电话和客服BPO方面的潜在商机，用于销售拜访前判断。`;
    observations.scenario = {
      company_name: companyName,
      demand_direction: demandDirection,
      industry,
      region,
      research_depth: researchDepth,
    };
    saveObservations();
    await page.getByPlaceholder(/某某市政务服务中心需要升级智能客服系统/).fill(prompt);
    await page.getByRole("button", { name: "解析需求", exact: true }).click();
    await expect(page.getByPlaceholder("例如：中国移动通信集团")).toBeVisible({ timeout: 180_000 });

    const interpreted = {
      company_name: await page.getByPlaceholder("例如：中国移动通信集团").inputValue(),
      demand_direction: await page.getByPlaceholder("例如：智能客服升级").inputValue(),
      industry: await page.getByPlaceholder("政务、医疗、金融等").inputValue(),
      region: await page.getByPlaceholder("可选").inputValue(),
      skill: await page.locator("#runtime-skill").inputValue(),
    };
    observations.interpretation = interpreted;
    saveObservations();
    await capture(page, testInfo, "04-interpreted-form");

    await page.getByPlaceholder("例如：中国移动通信集团").fill(companyName);
    await page.getByPlaceholder("例如：智能客服升级").fill(demandDirection);
    await page.getByPlaceholder("政务、医疗、金融等").fill(industry);
    await page.getByPlaceholder("可选").fill(region);

    const targetSkill = page.locator('#runtime-skill option[value="analyzing-contact-center-opportunities"]');
    if (await targetSkill.count()) {
      await page.locator("#runtime-skill").selectOption("analyzing-contact-center-opportunities");
    }
    await page.getByRole("button", { name: /销售极简版/ }).click();
    await page.getByRole(
      "button",
      {
        name: researchDepth === "standard"
          ? /^标准版 默认模式，质量和成本平衡$/
          : /^快速版 5-10 分钟，核心结论和商机卡$/,
      },
    ).click();
    await page.getByRole("button", { name: "生成调研计划", exact: true }).click();
    await expect(page.getByRole("button", { name: "确认创建任务", exact: true })).toBeVisible({ timeout: 180_000 });
    await capture(page, testInfo, "05-research-plan");

    await page.getByRole("button", { name: "确认创建任务", exact: true }).click();
    await page.waitForURL(/\/tasks\/[0-9a-f-]+/i, { timeout: 60_000 });
    taskId = page.url().match(/\/tasks\/([^/?#]+)/)?.[1] || "";
    expect(taskId).not.toBe("");
    observations.task_id = taskId;
    observations.task_created_at = new Date().toISOString();
    saveObservations();
    await capture(page, testInfo, "06-task-created");
  }

  const terminalStatuses = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELED"]);
  const timeline: Array<{ at: string; status: string; stage: string; progress: number }> = [];
  let finalTask: Record<string, unknown> | null = null;
  let clarificationCount = 0;
  const deadline = Date.now() + 18 * 60_000;

  while (Date.now() < deadline) {
    const taskResponse = await page.request.get(`/api/tasks/${taskId}`);
    expect(taskResponse.ok()).toBeTruthy();
    const task = await taskResponse.json();
    const state = {
      at: new Date().toISOString(),
      status: String(task.status || ""),
      stage: String(task.current_stage || ""),
      progress: Number(task.progress || 0),
    };
    const previous = timeline[timeline.length - 1];
    if (!previous || previous.status !== state.status || previous.stage !== state.stage || previous.progress !== state.progress) {
      timeline.push(state);
      console.log(`[user-journey] ${state.status} / ${state.stage} / ${state.progress}%`);
    }

    const clarification = page.getByText("等待你的确认", { exact: true });
    if (await clarification.isVisible().catch(() => false)) {
      clarificationCount += 1;
      await capture(page, testInfo, `07-clarification-${clarificationCount}`);
      const recommended = page.getByRole("button", { name: /按推荐假设继续/ }).first();
      if (await recommended.isVisible().catch(() => false)) {
        await recommended.click();
      } else {
        const confirmTarget = page.getByRole("button", { name: /确认该主体/ }).first();
        await expect(confirmTarget).toBeVisible();
        await confirmTarget.click();
      }
      await page.waitForTimeout(2_000);
    }

    if (terminalStatuses.has(state.status)) {
      finalTask = task;
      break;
    }
    await page.waitForTimeout(10_000);
  }

  observations.execution = {
    timeline,
    clarification_count: clarificationCount,
    final_task: finalTask,
  };
  saveObservations();
  expect(finalTask, "快速任务 18 分钟内应到达终态").not.toBeNull();
  expect(["COMPLETED", "PARTIAL"]).toContain(String(finalTask?.status));
  await capture(page, testInfo, "08-task-terminal");

  const researchPlanResponse = await page.request.get(`/api/tasks/${taskId}/research-plan`);
  expect(researchPlanResponse.ok()).toBeTruthy();
  const researchPlan = await researchPlanResponse.json();
  expect(researchPlan.plan_version).toBeGreaterThanOrEqual(1);
  expect(researchPlan.goals?.length).toBeGreaterThan(0);
  expect(researchPlan.tasks?.length).toBeGreaterThan(0);
  const exactQueries = researchPlan.tasks.flatMap(
    (task: { search_strategy?: { queries?: string[] } }) => task.search_strategy?.queries || [],
  );
  expect(exactQueries.length).toBeGreaterThan(0);
  observations.research_plan = {
    plan_version: researchPlan.plan_version,
    goal_count: researchPlan.goals.length,
    task_count: researchPlan.tasks.length,
    exact_query_count: exactQueries.length,
    replan_count: researchPlan.replan_count,
  };
  await expect(page.getByText("商业分析总目标")).toBeVisible();
  saveObservations();

  let report: Record<string, unknown> | null = null;
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const response = await page.request.get(`/api/reports/${taskId}`);
    if (response.ok()) {
      report = await response.json();
      break;
    }
    await page.waitForTimeout(5_000);
  }
  expect(report, "终态后 60 秒内应可读取正式报告").not.toBeNull();

  const content = String(report?.content_md || "");
  const evidenceIndex = (report?.evidence_index || {}) as Record<string, unknown>;
  const evidenceCount = Number(
    evidenceIndex.count ??
      (Array.isArray(evidenceIndex.ids) ? evidenceIndex.ids.length : 0),
  );
  const reportQuality = {
    content_length: content.length,
    evidence_count: evidenceCount,
    has_core_conclusion: /核心结论|客户作战卡|商机评级/.test(content),
    has_current_state: /企业现状|现状判断|能力地图/.test(content),
    has_opportunity_action: /商机|下一步|行动建议|销售问诊/.test(content),
  };
  observations.report_quality = reportQuality;
  fs.writeFileSync(path.join(artifactsDir, `report-${taskId}.md`), content, "utf8");
  fs.writeFileSync(path.join(artifactsDir, `report-${taskId}.json`), JSON.stringify(report, null, 2), "utf8");
  saveObservations();

  expect(reportQuality.content_length).toBeGreaterThan(1_500);
  expect(reportQuality.has_core_conclusion).toBe(true);
  expect(reportQuality.has_current_state).toBe(true);
  expect(reportQuality.has_opportunity_action).toBe(true);

  for (const tabName of ["分析报告", "证据回溯", "证据审计", "产品匹配", "体验式背调"]) {
    const tab = page.getByRole("button", { name: tabName, exact: true });
    await expect(tab).toBeVisible();
    await tab.click();
    await page.waitForTimeout(600);
  }
  await page.getByRole("button", { name: "分析报告", exact: true }).click();
  await capture(page, testInfo, "09-report");

  const exports: Record<string, { bytes: number }> = {};
  for (const [label, extension] of [["导出 PDF", "pdf"], ["导出 Word", "docx"]] as const) {
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 120_000 }),
      page.getByRole("button", { name: label, exact: true }).click(),
    ]);
    const exportPath = path.join(artifactsDir, `report-${taskId}.${extension}`);
    await download.saveAs(exportPath);
    const bytes = fs.statSync(exportPath).size;
    expect(bytes).toBeGreaterThan(1_000);
    exports[extension] = { bytes };
  }

  await page.getByRole("link", { name: "历史记录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "历史任务", exact: true })).toBeVisible();
  await expect(page.getByText("上海银行", { exact: false }).first()).toBeVisible();
  await capture(page, testInfo, "10-history");

  observations.completed_journey = {
    exports,
    history_contains_task: true,
    ...browserFailures,
  };
  saveObservations();
  expect(browserFailures.serverErrors).toEqual([]);
});
