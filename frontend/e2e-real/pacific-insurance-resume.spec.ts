import { test, expect, request, type APIRequestContext, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

// ═══════════════════════════════════════════════════════════════
// 续跑：回答阻塞澄清 → 等待报告生成 → 报告验收
// 前置：pacific-insurance.spec.ts 已创建任务且任务因澄清暂停
// ═══════════════════════════════════════════════════════════════

const ARTIFACTS = path.join(__dirname, 'artifacts');
const SHOTS = path.join(ARTIFACTS, 'screenshots');
const STATE_PATH = path.join(__dirname, '.auth', 'storageState.json');
const POLL_INTERVAL_MS = 15_000;
const POLL_TIMEOUT_MS = 30 * 60_000;

const obs: Record<string, unknown> = { resume_started_at: new Date().toISOString() };

function saveObs() {
  fs.writeFileSync(path.join(ARTIFACTS, 'observations_resume.json'), JSON.stringify(obs, null, 2));
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true }).catch(() => {});
}

function readTaskId(): string {
  const prev = JSON.parse(fs.readFileSync(path.join(ARTIFACTS, 'observations.json'), 'utf-8'));
  if (!prev.task_id) throw new Error('observations.json 中没有 task_id，请先运行主流程测试');
  return prev.task_id;
}

let api: APIRequestContext;
const baseURL = process.env.REAL_BASE_URL || 'https://127.0.0.1:10443';

test.beforeAll(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  api = await request.newContext({ baseURL });
  const login = await api.post('/api/auth/login', {
    form: { username: 'admin', password: 'admin123' },
  });
  if (!login.ok()) throw new Error(`登录失败: ${login.status()}`);
  await api.storageState({ path: STATE_PATH });
});

test.afterAll(async () => {
  obs.finished_at = new Date().toISOString();
  saveObs();
  await api?.dispose();
});

test('回答澄清并等待报告完成', async ({ browser }) => {
  test.setTimeout(POLL_TIMEOUT_MS + 10 * 60_000);
  const taskId = readTaskId();
  obs.task_id = taskId;
  const context = await browser.newContext({ storageState: STATE_PATH });
  const page = await context.newPage();

  // ── 1) 刷新任务页，验证澄清面板是否出现 ──────────────────────
  await test.step('刷新页面查看任务真实状态与澄清面板', async () => {
    await page.goto(`/tasks/${taskId}`);
    await page.waitForTimeout(4_000);
    await shot(page, '11-reload-task-page');
    const pageText = await page.locator('body').innerText();
    obs.reload_page = {
      shows_queued_stuck: pageText.includes('queued'),
      shows_clarification: pageText.includes('等待你的确认'),
      progress_text: /(\d+)\s*%/.exec(pageText)?.[0] ?? null,
    };
    saveObs();
  });

  // ── 2) 回答澄清：确认目标主体 ────────────────────────────────
  await test.step('回答阻塞澄清（确认该主体）', async () => {
    const optionBtn = page.getByRole('button', { name: /确认该主体/ });
    await expect(optionBtn).toBeVisible({ timeout: 30_000 });
    await optionBtn.click();
    await page.waitForTimeout(3_000);
    await shot(page, '12-clarification-answered');
    obs.clarification_answered = { option: 'CONFIRM_TARGET', ts: new Date().toISOString() };
    saveObs();
  });

  // ── 3) 轮询至终态 ────────────────────────────────────────────
  await test.step('等待任务到达终态', async () => {
    const stages: { ts: string; status: string; stage: string; progress: number }[] = [];
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    let finalStatus = '';
    while (Date.now() < deadline) {
      const resp = await api.get(`/api/tasks/${taskId}`);
      if (!resp.ok()) throw new Error(`轮询失败: ${resp.status()}`);
      const t = await resp.json();
      const last = stages[stages.length - 1];
      if (!last || last.stage !== t.current_stage || last.status !== t.status) {
        stages.push({ ts: new Date().toISOString(), status: t.status, stage: t.current_stage, progress: t.progress });
        console.log(`[stage] ${t.status} / ${t.current_stage} / ${t.progress}%`);
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
    saveObs();
    await shot(page, '13-task-final');
    if (!finalStatus) throw new Error(`任务 30 分钟内仍未到达终态（卡在 ${stages[stages.length - 1]?.stage}）`);
    expect(['COMPLETED', 'PARTIAL']).toContain(finalStatus);
  });

  // ── 4) 报告验收 ──────────────────────────────────────────────
  await test.step('报告内容与导出验收', async () => {
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

    // UI Tab 验收
    await page.goto(`/tasks/${taskId}`);
    await page.waitForTimeout(3_000);
    for (const [tab, name] of [['分析报告', '14-report-tab'], ['证据回溯', '15-evidences-tab'], ['证据审计', '16-audit-tab']] as const) {
      const btn = page.getByRole('button', { name: tab });
      if (await btn.isVisible().catch(() => false)) {
        await btn.click();
        await page.waitForTimeout(1_500);
        await shot(page, name);
      } else {
        obs[`tab_${tab}_missing`] = true;
      }
    }

    // 导出 PDF / DOCX
    obs.exports = {};
    const reportTab = page.getByRole('button', { name: '分析报告' });
    if (await reportTab.isVisible().catch(() => false)) await reportTab.click();
    await page.waitForTimeout(1_000);
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
        const r = await api.get(`/api/reports/${taskId}/${kind}`);
        (obs.exports as Record<string, unknown>)[kind] = { ui_download: false, api_status: r.status() };
        if (r.ok()) {
          const file = path.join(ARTIFACTS, `report_${taskId}.${kind}`);
          fs.writeFileSync(file, await r.body());
          ((obs.exports as Record<string, unknown>)[kind] as Record<string, unknown>).bytes = fs.statSync(file).size;
        }
      }
    }

    const logs = await api.get(`/api/tasks/${taskId}/logs`);
    if (logs.ok()) {
      fs.writeFileSync(path.join(ARTIFACTS, `logs_${taskId}.json`), JSON.stringify(await logs.json(), null, 2));
    }
    saveObs();
    await shot(page, '17-done');
  });
});
