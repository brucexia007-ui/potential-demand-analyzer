"use client";

import { useCallback, useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/auth";

type Goal = {
  goal_id: string;
  parent_id: string | null;
  question: string;
  rationale: string;
  priority: string;
  required: boolean;
  status: string;
};

type PlannedTask = {
  task_id: string;
  goal_ids: string[];
  title: string;
  question: string;
  rationale: string;
  skill_name: string;
  evidence_usage: string;
  search_strategy: {
    target_content?: string[];
    preferred_sources?: string[];
    queries?: string[];
  } | null;
  dependencies: string[];
  status: string;
  success_conditions: string[];
  stop_conditions: string[];
};

type ResearchPlanView = {
  status: string;
  error_message?: string | null;
  plan_version: number | null;
  primary_goal_id: string | null;
  goals: Goal[];
  tasks: PlannedTask[];
  versions: {
    plan_id: string;
    plan_version: number;
    status: string;
    created_at: string;
  }[];
};

const STATUS_LABELS: Record<string, string> = {
  NOT_STARTED: "等待启动",
  PLANNING: "LLM 正在规划",
  PLANNING_FAILED: "规划失败",
  APPROVED: "计划已批准",
  SUPERSEDED: "已被新计划替代",
  COMPLETED: "研究任务已完成",
  PENDING: "等待前置任务",
  MATERIALIZED: "已进入执行队列",
  RUNNING: "执行中",
  BLOCKED: "需要处理",
  CANCELLED: "已取消",
  ANSWERED: "已回答",
};

function statusLabel(value: string): string {
  return STATUS_LABELS[value] || value;
}

export function ResearchDirectorPlanPanel({ taskId }: { taskId: string }) {
  const [plan, setPlan] = useState<ResearchPlanView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await authenticatedFetch(`/api/tasks/${taskId}/research-plan`);
    if (!response.ok) throw new Error(`研究计划加载失败 (${response.status})`);
    setPlan(await response.json() as ResearchPlanView);
    setError(null);
  }, [taskId]);

  useEffect(() => {
    void load().catch((reason) => {
      setError(reason instanceof Error ? reason.message : "研究计划加载失败");
    });
    const timer = window.setInterval(() => {
      void load().catch(() => undefined);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [load]);

  if (error) {
    return <p className="text-sm text-red-600">{error}</p>;
  }
  if (plan?.status === "PLANNING_FAILED") {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <p className="font-medium text-red-900">Research Director 规划失败</p>
        <p className="mt-1 text-sm text-red-700">
          {plan.error_message || "LLM 连续两次未能生成可执行计划，请检查模型配置或重新发起任务。"}
        </p>
      </div>
    );
  }
  if (!plan || plan.status === "NOT_STARTED" || plan.status === "PLANNING") {
    return (
      <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
        <p className="font-medium text-blue-900">Research Director 正在构建研究计划</p>
        <p className="mt-1 text-sm text-blue-700">
          LLM 会先明确商业分析目标，再决定任务、来源和精确搜索内容。
        </p>
      </div>
    );
  }

  const primaryGoal = plan.goals.find((goal) => goal.goal_id === plan.primary_goal_id);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-neutral-500">
            商业分析总目标
          </p>
          <h3 className="mt-1 text-lg font-semibold text-neutral-950">
            {primaryGoal?.question || "研究目标"}
          </h3>
          {primaryGoal?.rationale && (
            <p className="mt-1 text-sm text-neutral-600">{primaryGoal.rationale}</p>
          )}
        </div>
        <span className="rounded-full bg-neutral-950 px-3 py-1 text-xs font-medium text-white">
          V{plan.plan_version} · {statusLabel(plan.status)}
        </span>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold text-neutral-800">目标树</h4>
        <div className="grid gap-2 md:grid-cols-2">
          {plan.goals.map((goal) => (
            <div key={goal.goal_id} className="rounded-lg border border-neutral-200 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-neutral-500">
                  {goal.goal_id}{goal.required ? " · 必答" : ""}
                </span>
                <span className="text-xs text-neutral-500">{statusLabel(goal.status)}</span>
              </div>
              <p className="mt-1 text-sm font-medium text-neutral-900">{goal.question}</p>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold text-neutral-800">任务 DAG</h4>
        <div className="space-y-3">
          {plan.tasks.map((task) => (
            <div key={task.task_id} className="rounded-lg border border-neutral-200 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-neutral-950">
                    {task.task_id} · {task.title}
                  </p>
                  <p className="mt-1 text-sm text-neutral-600">{task.question}</p>
                </div>
                <span className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-700">
                  {statusLabel(task.status)}
                </span>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs text-neutral-600">
                <span className="rounded bg-neutral-100 px-2 py-1">
                  服务目标：{task.goal_ids.join("、")}
                </span>
                <span className="rounded bg-neutral-100 px-2 py-1">
                  证据用途：{task.evidence_usage === "TARGET_FACT" ? "目标企业事实" : "背景"}
                </span>
                <span className="rounded bg-neutral-100 px-2 py-1">
                  前置：{task.dependencies.length ? task.dependencies.join("、") : "无"}
                </span>
              </div>
              {task.search_strategy?.queries?.length ? (
                <details className="mt-3">
                  <summary className="cursor-pointer text-sm font-medium text-neutral-700">
                    查看 LLM 决定的搜索内容
                  </summary>
                  <div className="mt-2 rounded-lg bg-neutral-50 p-3">
                    <p className="text-xs text-neutral-500">目标内容</p>
                    <p className="mt-1 text-sm text-neutral-700">
                      {(task.search_strategy.target_content || []).join("、")}
                    </p>
                    <p className="mt-3 text-xs text-neutral-500">精确查询</p>
                    <ul className="mt-1 space-y-1">
                      {task.search_strategy.queries.map((query) => (
                        <li key={query} className="break-all font-mono text-xs text-neutral-700">
                          {query}
                        </li>
                      ))}
                    </ul>
                  </div>
                </details>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      {plan.versions.length > 1 && (
        <p className="text-xs text-neutral-500">
          已发生 {plan.versions.length - 1} 次证据缺口重规划；旧任务和已执行查询均保留。
        </p>
      )}
    </div>
  );
}
