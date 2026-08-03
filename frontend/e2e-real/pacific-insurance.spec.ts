import { test, expect, request, type APIRequestContext, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

// ═══════════════════════════════════════════════════════════════
// 真实全流程 E2E：太平洋保险 · 客服中心升级改造场景
// 无任何 mock —— 真实 LLM（DeepSeek）+ 真实搜索（博查）。
// 产出物（截图/报告JSON/日志/导出文件）写入 e2e-real/artifacts/
// 供评估报告引用。
// ═══════════════════════════════════════════════════════════════

const AUTH_DIR = path.join(__dirname, '.auth');
const ARTIFACTS = path.join(__dirname, 'artifacts');
const SHOTS = path.join(ARTIFACTS, 'screenshots');
const STATE_PATH = path.join(AUTH_DIR, 'storageState.json');

const NLP_INPUT = '挖掘太平洋保险在客服中心升级改造场景下的潜在需求';
const POLL_INTERVAL_MS = 15_000;
const POLL_TIMEOUT_MS = 30 * 60_000;

// 登录态在测试体内手动创建（test.use 的 storageState 会先于 beforeAll 校验文件存在性）

let api: APIRequestContext;
let baseURL = 'https://127.0.0.1:10443';

// 全程观察记录，最终落盘 observations.json 供评估报告使用
const obs: Record<string, unknown> = {
  nlp_input: NLP_INPUT,
  started_at: new Date().toISOString(),
};

function saveObs() {
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  fs.writeFileSync(path.join(ARTIFACTS, 'observations.json'), JSON.stringify(obs, null, 2));
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true }).catch(() => {});
}

test.beforeAll(async () => {
  baseURL = process.env.REAL_BASE_URL || 'https://127.0.0.1:10443';
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });

  api = await request.newContext({ baseURL });

  // 1) 登录（HttpOnly Cookie）
  const login = await api.post('/api/auth/login', {
    form: { username: 'admin', password: 'admin123' },
  });
  if (!login.ok()) throw new Error(`登录失败: ${login.status()} ${await login.text()}`);

  // 2) 环境快照 + 执行就绪断言
  const statusResp = await api.get('/api/config/status');
  if (!statusResp.ok()) throw new Error(`config/status 失败: ${statusResp.status()}`);
  const status = await statusResp.json();
  obs.env_snapshot = status;
  if (!status.execution_ready) {
    throw new Error(`系统未执行就绪: ${JSON.stringify(status.blocking_items)}`);
  }

  // 3) 保存浏览器登录态
  await api.storageState({ path: STATE_PATH });
  saveObs();
});

test.afterAll(async () => {
  obs.finished_at = new Date().toISOString();
  saveObs();
  await api?.dispose();
});

test('太平洋保险客服中心升级改造 — 真实全流程', async ({ browser }) => {
  test.setTimeout(POLL_TIMEOUT_MS + 10 * 60_000);
  const context = await browser.newContext({ storageState: STATE_PATH });
  const page = await context.newPage();

  // ── Step 1: 首页 NLP 输入 + 解析 ─────────────────────────────
  await test.step('首页输入并解析需求', async () => {
    await page.goto('/');
    await shot(page, '01-home');
    const textarea = page.locator('textarea').first();
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill(NLP_INPUT);
    await page.getByRole('button', { name: '解析需求' }).click();

    // 解析完成 → 进入表单步（"生成调研计划"按钮出现）
    await expect(page.getByRole('button', { name: '生成调研计划' })).toBeVisible({ timeout: 120_000 });
    await shot(page, '02-form-prefilled');

    // 记录解析预填结果
    const selectedSkill = await page.locator('#runtime-skill').inputValue();
    expect(selectedSkill).toBe('analyzing-contact-center-opportunities');
    const inputs = page.locator('input[type="text"]');
    const values = await inputs.evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value));
    const lowConf = await page.locator('text=置信度较低').isVisible().catch(() => false);
    obs.interpret = {
      prefilled_values: values,
      low_confidence_banner: lowConf,
      selected_skill: selectedSkill,
    };
    saveObs();
  });

  // ── Step 2: 表单确认 → 生成调研计划 ──────────────────────────
  await test.step('表单确认并生成调研计划', async () => {
    await page.getByRole('button', { name: '生成调研计划' }).click();
    await expect(page.getByRole('button', { name: '确认创建任务' })).toBeVisible({ timeout: 120_000 });
    await shot(page, '03-plan-preview');
    obs.plan_preview_text = (await page.locator('main').innerText()).slice(0, 3000);
    saveObs();
  });

  // ── Step 3: 确认创建 → 跳转任务详情 ──────────────────────────
  let taskId = '';
  await test.step('确认创建并跳转任务详情页', async () => {
    await page.getByRole('button', { name: '确认创建任务' }).click();
    await page.waitForURL(/\/tasks\/[0-9a-f-]+/i, { timeout: 60_000 });
    taskId = page.url().split('/tasks/')[1].split(/[?#]/)[0];
    obs.task_id = taskId;
    obs.created_at = new Date().toISOString();
    saveObs();
    await shot(page, '04-task-created');
  });

  // ── Step 4: 轮询执行直至终态，记录阶段切换 ───────────────────
  await test.step('观察任务执行直至完成', async () => {
    const stages: { ts: string; status: string; stage: string; progress: number }[] = [];
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    let finalStatus = '';
    let clarificationHandled = false;

    while (Date.now() < deadline) {
      const resp = await api.get(`/api/tasks/${taskId}`);
      if (!resp.ok()) throw new Error(`轮询任务失败: ${resp.status()}`);
      const t = await resp.json();
      const last = stages[stages.length - 1];
      if (!last || last.stage !== t.current_stage || last.status !== t.status) {
        stages.push({
          ts: new Date().toISOString(),
          status: t.status,
          stage: t.current_stage,
          progress: t.progress,
        });
        console.log(`[stage] ${t.status} / ${t.current_stage} / ${t.progress}%`);
      }

      // 人工澄清介入：主体确认优先；其他澄清再使用明确的通用回答。
      if (!clarificationHandled) {
        const targetConfirmBtn = page.getByRole('button', { name: /确认该主体|确认该企业主体/ });
        const clarifyBtn = page.getByRole('button', { name: '提交完整回答并继续' });
        if (await targetConfirmBtn.isVisible().catch(() => false)) {
          clarificationHandled = true;
          obs.clarification = {
            appeared: true,
            phase: 'PRE_EXECUTION',
            answer: 'CONFIRM_TARGET',
            ts: new Date().toISOString(),
          };
          await shot(page, '05-clarification');
          await targetConfirmBtn.click();
          console.log('[clarification] 已在页面直接确认目标主体');
        } else if (await clarifyBtn.isVisible().catch(() => false)) {
          clarificationHandled = true;
          obs.clarification = {
            appeared: true,
            answer: 'PUBLIC_INFORMATION_ONLY',
            ts: new Date().toISOString(),
          };
          await shot(page, '05-clarification');
          const cta = page.locator('textarea').last();
          if (await cta.isVisible().catch(() => false)) {
            await cta.fill('无补充信息，请基于公开信息继续调研。');
          }
          await clarifyBtn.click().catch(() => {});
          console.log('[clarification] 已提交通用回答');
        }
      }

      if (['COMPLETED', 'FAILED', 'PARTIAL'].includes(t.status)) {
        finalStatus = t.status;
        obs.final_status = t.status;
        obs.error_message = t.error_message;
        break;
      }
      await page.waitForTimeout(POLL_INTERVAL_MS);
    }

    obs.stage_timeline = stages;
    obs.execution_completed_at = new Date().toISOString();
    saveObs();
    await shot(page, '06-task-final');

    if (!finalStatus) throw new Error(`任务 ${taskId} 30 分钟内未到达终态（卡在 ${stages[stages.length - 1]?.stage}）`);
    expect(['COMPLETED', 'PARTIAL']).toContain(finalStatus);
  });

  // ── Step 5: 报告验收 ─────────────────────────────────────────
  await test.step('报告内容与导出验收', async () => {
    // 拉取报告 JSON 存盘
    let report: Record<string, unknown> | null = null;
    for (let i = 0; i < 10; i++) {
      const r = await api.get(`/api/reports/${taskId}`);
      if (r.ok()) { report = await r.json(); break; }
      await page.waitForTimeout(6_000);
    }
    if (!report) throw new Error('任务完成后 60s 内报告仍不可用');
    fs.writeFileSync(path.join(ARTIFACTS, `report_${taskId}.json`), JSON.stringify(report, null, 2));
    fs.writeFileSync(path.join(ARTIFACTS, `report_${taskId}.md`), String(report.content_md ?? ''));
    const idx = (report.evidence_index ?? {}) as Record<string, unknown>;
    obs.report = {
      version_no: report.version_no,
      content_length: String(report.content_md ?? '').length,
      evidence_count: idx.count ?? (Array.isArray(idx.ids) ? idx.ids.length : null),
      validation: idx.validation ?? null,
      has_audit: !!idx.audit,
    };
    saveObs();

    // 报告 Tab 渲染
    await page.getByRole('button', { name: '分析报告' }).click();
    await page.waitForTimeout(2_000);
    await shot(page, '07-report-tab');

    // 证据回溯 Tab
    await page.getByRole('button', { name: '证据回溯' }).click();
    await page.waitForTimeout(1_500);
    await shot(page, '08-evidences-tab');

    // 证据审计 Tab
    await page.getByRole('button', { name: '证据审计' }).click();
    await page.waitForTimeout(1_500);
    await shot(page, '09-audit-tab');

    // 导出 PDF / Word（UI 点击 → 下载事件；失败则 API 兜底并记录）
    await page.getByRole('button', { name: '分析报告' }).click();
    await page.waitForTimeout(1_000);
    obs.exports = {};
    for (const [kind, btnName] of [['pdf', '导出 PDF'], ['docx', '导出 Word']] as const) {
      try {
        const [download] = await Promise.all([
          page.waitForEvent('download', { timeout: 90_000 }),
          page.getByRole('button', { name: btnName }).click(),
        ]);
        const file = path.join(ARTIFACTS, `report_${taskId}.${kind}`);
        await download.saveAs(file);
        (obs.exports as Record<string, unknown>)[kind] = { ui_download: true, bytes: fs.statSync(file).size };
      } catch {
        // API 兜底下载，同时记录 UI 导出问题
        const r = await api.get(`/api/reports/${taskId}/${kind}`);
        (obs.exports as Record<string, unknown>)[kind] = { ui_download: false, api_status: r.status() };
        if (r.ok()) {
          const file = path.join(ARTIFACTS, `report_${taskId}.${kind}`);
          fs.writeFileSync(file, await r.body());
          ((obs.exports as Record<string, unknown>)[kind] as Record<string, unknown>).bytes = fs.statSync(file).size;
        }
      }
    }

    // 全量日志存盘
    const logs = await api.get(`/api/tasks/${taskId}/logs`);
    if (logs.ok()) {
      fs.writeFileSync(path.join(ARTIFACTS, `logs_${taskId}.json`), JSON.stringify(await logs.json(), null, 2));
    }
    saveObs();
    await shot(page, '10-done');
  });
});
