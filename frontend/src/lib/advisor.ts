/** v3.1: Advisor API 调用 */

import { authenticatedFetch } from "@/lib/auth";

// ── 类型 ──────────────────────────────────────────────────────────────

export type InterpretResult = {
  company_name: string;
  demand_direction: string;
  industry: string | null;
  region: string | null;
  business_goal: string | null;
  time_range: string | null;
  suggested_skill: string | null;
  confidence: number;
  missing_fields: string[];
  raw_llm_output?: string | null;
};

export type PlanResult = {
  analysis_objective: string;
  decision_questions: string[];
  suggested_depth: string;
  candidate_focus: string[];
  suggested_complexity: string;
  planning_mode: "llm_research_director";
  budget_guardrails: {
    max_search_queries: number;
    max_fetches: number;
    max_replan_rounds: number;
  };
  reasoning: string;
  raw_llm_output?: string | null;
};

export type CreateTaskPayload = {
  target_account_id: string;
  demand_direction: string;
  industry?: string | null;
  region?: string | null;
  business_goal?: string | null;
  skill_id?: string | null;
  report_profile?: string | null;
  depth?: string;
  focus_modules?: string[];
  time_range?: string | null;
  known_clues?: Record<string, unknown>[];
  user_constraints?: Record<string, unknown>;
  expected_outputs?: string[];
  enable_field_agent?: boolean;
  raw_input?: string | null;
};

export type CreateTaskResult = {
  task_id: string;
  brief_id: string | null;
  status: string;
  execution_mode: string;
};

// ── API 调用 ───────────────────────────────────────────────────────────

function apiHeaders(): Record<string, string> {
  return { "Content-Type": "application/json" };
}

/** 解析自然语言输入 */
export async function interpretInput(text: string): Promise<InterpretResult> {
  const res = await authenticatedFetch("/api/advisor/interpret", {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({ input_text: text }),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => null);
    throw new Error(detail || `解析失败 (${res.status})`);
  }
  return res.json();
}

/** 获取执行计划建议 */
export async function planTask(payload: {
  company_name: string;
  demand_direction: string;
  industry?: string | null;
  region?: string | null;
  business_goal?: string | null;
  depth?: string;
}): Promise<PlanResult> {
  const res = await authenticatedFetch("/api/advisor/plan", {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => null);
    throw new Error(detail || `计划生成失败 (${res.status})`);
  }
  return res.json();
}

/** 创建任务 */
export async function createTask(payload: CreateTaskPayload): Promise<CreateTaskResult> {
  const res = await authenticatedFetch("/api/advisor/create-task", {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => null);
    throw new Error(detail || `创建任务失败 (${res.status})`);
  }
  return res.json();
}
