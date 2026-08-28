import { expect, test, type Page } from "@playwright/test";
import { mockNotificationRoutes } from "./mocks/setup";

const TASK_ID = "10000000-0000-0000-0000-000000000099";

function executionView(observedState: "QUEUED" | "WAITING_FOR_INPUT" | "PARTIAL") {
  return {
    task_id: TASK_ID,
    desired_state: "RUNNING",
    observed_state: observedState,
    control_version: observedState === "QUEUED" ? 1 : 2,
    active_run: {
      id: "20000000-0000-0000-0000-000000000099",
      generation: 1,
      status: observedState,
      started_at: "2026-07-24T01:00:00Z",
    },
    dimensions: [{
      dimension: "mapping-contact-center-footprint",
      total_units: 10,
      completed_units: observedState === "QUEUED" ? 0 : 9,
      remaining_units: observedState === "QUEUED" ? 10 : 1,
      status_counts: {},
    }],
    remaining_work_units: observedState === "QUEUED" ? 10 : 1,
    budget: {
      reserved_amount: 0,
      settled_amount: 0,
      refunded_amount: 0,
      net_reserved_amount: 0,
      currencies: [],
      settlement_count: 0,
      settled_token_count: 0,
    },
    latest_heartbeat_at: "2026-07-24T01:04:00Z",
    latest_checkpoint: null,
    recovery_count: 0,
    eta: null,
  };
}

async function mockSharedRoutes(page: Page) {
  await mockNotificationRoutes(page);
  await page.route(`**/api/tasks/${TASK_ID}/logs`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ logs: [] }),
    });
  });
  await page.route(`**/api/tasks/${TASK_ID}/execution/events/stream?*`, async (route) => {
    await route.fulfill({ status: 503, body: "SSE unavailable" });
  });
  await page.route(`**/api/tasks/${TASK_ID}/execution/events?*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ events: [] }),
    });
  });
}

test.describe("任务详情实时刷新", () => {
  test("SSE 失效时仍通过轮询展示阻塞澄清", async ({ page }) => {
    let taskRequestCount = 0;
    await mockSharedRoutes(page);
    await page.route(new RegExp(`/api/tasks/${TASK_ID}$`), async (route) => {
      taskRequestCount += 1;
      const waiting = taskRequestCount >= 2;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          task_id: TASK_ID,
          company_name: "太平洋保险",
          demand_direction: "客服中心商机分析",
          status: waiting ? "PAUSED" : "PENDING",
          current_stage: waiting ? "等待目标主体确认" : "准备执行",
          progress: waiting ? 92 : 0,
          error_message: null,
          created_at: "2026-07-24T01:00:00Z",
          updated_at: waiting ? "2026-07-24T01:04:00Z" : "2026-07-24T01:00:00Z",
        }),
      });
    });
    await page.route(`**/api/tasks/${TASK_ID}/execution`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(executionView(taskRequestCount >= 2 ? "WAITING_FOR_INPUT" : "QUEUED")),
      });
    });
    await page.route(`**/api/tasks/${TASK_ID}/clarifications`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(taskRequestCount >= 2 ? [{
          id: "30000000-0000-0000-0000-000000000099",
          task_id: TASK_ID,
          phase: "PRE_EXECUTION",
          category: "TARGET_ENTITY",
          materiality: "BLOCKING",
          question: "请确认研究主体是否为中国太平洋保险（集团）股份有限公司。",
          options: [{
            code: "CONFIRM_TARGET",
            label: "确认该主体",
            impact: "按当前主体继续研究。",
          }],
          recommended_option: null,
          impact: "避免证据错绑。",
          status: "OPEN",
          control_version: 1,
        }] : []),
      });
    });

    await page.goto(`/tasks/${TASK_ID}`);
    await expect(page.getByText("等待你的确认")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "确认该主体" })).toBeVisible();
    expect(taskRequestCount).toBeGreaterThanOrEqual(2);
  });

  test("PARTIAL 是终态，暂停、继续、取消全部不可用", async ({ page }) => {
    await mockSharedRoutes(page);
    await page.route(new RegExp(`/api/tasks/${TASK_ID}$`), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          task_id: TASK_ID,
          company_name: "太平洋保险",
          demand_direction: "客服中心商机分析",
          status: "PARTIAL",
          current_stage: "执行结束",
          progress: 100,
          error_message: null,
          created_at: "2026-07-24T01:00:00Z",
          updated_at: "2026-07-24T01:04:00Z",
        }),
      });
    });
    await page.route(`**/api/tasks/${TASK_ID}/execution`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(executionView("PARTIAL")),
      });
    });
    await page.route(`**/api/tasks/${TASK_ID}/clarifications`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    });
    await page.route(new RegExp(`/api/reports/${TASK_ID}$`), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          report_id: "40000000-0000-0000-0000-000000000099",
          task_id: TASK_ID,
          version_id: "50000000-0000-0000-0000-000000000099",
          version_no: 1,
          content_md: "# 部分报告",
          evidence_index: {},
          created_at: "2026-07-24T01:04:00Z",
        }),
      });
    });
    await page.route(new RegExp(`/api/reports/${TASK_ID}/evidences$`), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          evidences: [
            {
              id: "60000000-0000-0000-0000-000000000091",
              dimension: "mapping-contact-center-footprint",
              title: "客服中心能力基线",
              snippet: "已发现公开服务入口。",
              url: "https://example.com/footprint",
              source_type: "official",
              meta_data: {},
              published_at: null,
              captured_at: "2026-07-24T01:00:00Z",
            },
            {
              id: "60000000-0000-0000-0000-000000000092",
              dimension: "evidence-recovery",
              title: "低准入证据补检",
              snippet: "已完成一次定向补检。",
              url: "https://example.com/recovery",
              source_type: "official",
              meta_data: {},
              published_at: null,
              captured_at: "2026-07-24T01:00:00Z",
            },
          ],
        }),
      });
    });

    await page.goto(`/tasks/${TASK_ID}`);
    await page.getByRole("button", { name: "证据回溯" }).click();
    await expect(page.getByRole("button", { name: "客服中心现状与能力基线", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "低准入证据补检", exact: true })).toBeVisible();
    await expect(page.getByText("其他分析维度")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "暂停", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "继续", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "取消", exact: true })).toBeDisabled();
  });
});
