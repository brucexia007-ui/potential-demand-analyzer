"use client";

import { useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/auth";

// ── 类型 ────────────────────────────────────────────────────────────────────

type FieldAgentRun = {
  id: string;
  task_id: string;
  agent_type: string;
  target_url: string | null;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  step_count: number;
  screenshot_paths: string[];
  visited_urls: string[];
  observations: string | null;
  blocked_reason: string | null;
  evidence_ids: string[];
  created_at: string | null;
};

type Props = {
  taskId: string;
};

// ── 状态样式 ────────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, { badge: string; label: string }> = {
  OK: { badge: "bg-green-100 text-green-700", label: "成功" },
  BLOCKED: { badge: "bg-red-100 text-red-700", label: "已拦截" },
  ERROR: { badge: "bg-red-100 text-red-700", label: "失败" },
  EMPTY: { badge: "bg-yellow-100 text-yellow-700", label: "空结果" },
  PENDING: { badge: "bg-blue-100 text-blue-700", label: "执行中" },
};

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString("zh-CN");
  } catch {
    return iso;
  }
}

// ── 组件 ────────────────────────────────────────────────────────────────────

export function FieldAgentPanel({ taskId }: Props) {
  const [runs, setRuns] = useState<FieldAgentRun[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    setIsLoading(true);
    authenticatedFetch(`/api/tasks/${taskId}/field-agent-runs`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setRuns(data.runs || []);
      })
      .catch(() => setRuns([]))
      .finally(() => setIsLoading(false));
  }, [taskId]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="mr-3 h-5 w-5 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
        <span className="text-sm text-neutral-500">加载背调记录...</span>
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="py-12 text-center">
        <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-lg border border-neutral-950/10 bg-white text-xs font-semibold text-neutral-500">
          NIL
        </div>
        <p className="text-sm text-neutral-600">暂无体验式背调记录</p>
        <p className="mt-1 text-xs text-neutral-400">
          此任务可能未启用网页体验背调功能
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-neutral-700">
          体验式背调记录
          <span className="ml-2 text-xs font-normal text-neutral-500">
            共 {runs.length} 次执行
          </span>
        </h3>
      </div>

      <div className="space-y-4">
        {runs.map((run) => {
          const isExpanded = expandedId === run.id;
          const statusStyle = STATUS_STYLE[run.status] || STATUS_STYLE.ERROR;

          return (
            <div
              key={run.id}
              className="overflow-hidden rounded-lg border border-neutral-950/10 bg-white"
            >
              {/* 运行摘要头部 */}
              <button
                onClick={() => setExpandedId(isExpanded ? null : run.id)}
                className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-neutral-50/50"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${statusStyle.badge}`}>
                    {statusStyle.label}
                  </span>
                  <span className="text-sm text-neutral-900 truncate max-w-[300px]">
                    {run.target_url || "无 URL"}
                  </span>
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <span className="text-xs text-neutral-400">
                    {run.step_count} 步
                  </span>
                  <span className="text-xs text-neutral-300">
                    {isExpanded ? "▲" : "▼"}
                  </span>
                </div>
              </button>

              {/* 展开详情 */}
              {isExpanded && (
                <div className="border-t border-neutral-950/10 px-4 py-4 space-y-4">
                  {/* 基本信息 */}
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <span className="text-neutral-500">开始时间: </span>
                      <span className="text-neutral-800">{formatTime(run.started_at)}</span>
                    </div>
                    <div>
                      <span className="text-neutral-500">结束时间: </span>
                      <span className="text-neutral-800">{formatTime(run.finished_at)}</span>
                    </div>
                    <div>
                      <span className="text-neutral-500">操作步数: </span>
                      <span className="text-neutral-800">{run.step_count}</span>
                    </div>
                    <div>
                      <span className="text-neutral-500">关联证据: </span>
                      <span className="text-neutral-800 font-mono">
                        {run.evidence_ids.length > 0
                          ? `${run.evidence_ids.length} 条`
                          : "无"}
                      </span>
                    </div>
                  </div>

                  {/* 访问 URL 列表 */}
                  {run.visited_urls.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-neutral-500 mb-1.5">
                        访问页面 ({run.visited_urls.length})
                      </p>
                      <div className="space-y-1">
                        {run.visited_urls.map((url, i) => (
                          <a
                            key={i}
                            href={url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="block text-xs text-blue-600 hover:text-blue-800 truncate"
                          >
                            {url}
                          </a>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 截图路径 */}
                  {run.screenshot_paths.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-neutral-500 mb-1.5">
                        截图 ({run.screenshot_paths.length})
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {run.screenshot_paths.map((path, i) => (
                          <span
                            key={i}
                            className="rounded border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] font-mono text-neutral-600"
                          >
                            {path}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 观察结论 */}
                  {run.observations && (
                    <div className="rounded border border-neutral-950/10 bg-neutral-50 p-3">
                      <p className="text-xs font-medium text-neutral-500 mb-1">
                        观察结论
                      </p>
                      <p className="text-sm text-neutral-700 leading-relaxed whitespace-pre-wrap">
                        {run.observations}
                      </p>
                    </div>
                  )}

                  {/* 阻断原因 */}
                  {run.blocked_reason && (
                    <div className="rounded border border-red-200 bg-red-50 p-3">
                      <p className="text-xs font-medium text-red-600 mb-1">
                        安全阻断
                      </p>
                      <p className="text-sm text-red-700">
                        {run.blocked_reason}
                      </p>
                    </div>
                  )}

                  {/* 关联证据 ID 列表 */}
                  {run.evidence_ids.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-neutral-500 mb-1">
                        关联证据 ID
                      </p>
                      <div className="flex flex-wrap gap-1">
                        {run.evidence_ids.map((eid, i) => (
                          <span
                            key={i}
                            className="rounded bg-neutral-950/5 px-1.5 py-0.5 text-[10px] font-mono text-neutral-600"
                          >
                            {typeof eid === "string" ? eid.slice(0, 8) : String(eid)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
