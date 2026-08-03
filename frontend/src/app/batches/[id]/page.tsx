"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell, SegmentedControl, StatusBadge } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";
import { BatchExecutionProgress, type BatchDiscoveryRow } from "@/app/components/batch-progress";

type BatchDetail = {
  batch_id: string;
  name: string;
  status: string;
  root_skill_name: string;
  research_mode: string;
  capability_profile_id: string | null;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  cancelled_tasks: number;
  paused_tasks: number;
  running_tasks: number;
  partial_tasks: number;
  paused?: boolean;  // WBS-9
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  tasks: SubTaskItem[];
  tasks_total: number;
  tasks_page: number;
  tasks_page_size: number;
  import_rows: BatchDiscoveryRow[];
  import_rows_total: number;
  accepted_rows: number;
  rejected_rows: number;
};

type SubTaskItem = {
  task_id: string;
  company_name: string;
  demand_direction: string;
  status: string;
  desired_state: string;
  observed_state: string;
  created_at: string;
};

type BatchSummary = {
  batch_id: string;
  name: string;
  status: string;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  cancelled_tasks: number;
  paused_tasks: number;
  running_tasks: number;
  partial_tasks: number;
  paused?: boolean;  // WBS-9
};

function statusLabel(s: string): string {
  const labels: Record<string, string> = {
    PENDING: "等待中",
    RUNNING: "执行中",
    COMPLETED: "已完成",
    FAILED: "已失败",
    CANCELLED: "已取消",
    PARTIAL: "部分完成",
  };
  return labels[s] || s;
}

const TASK_STATUS_TABS = [
  { value: "", label: "全部" },
  { value: "RUNNING", label: "执行中" },
  { value: "COMPLETED", label: "已完成" },
  { value: "FAILED", label: "已失败" },
];

export default function BatchDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { user, isLoading, authState } = useAuth();
  const { error: toastError, success: toastSuccess } = useToast();
  const batchId = params.id as string;

  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [tasksStatus, setTasksStatus] = useState("");
  const [tasksPage, setTasksPage] = useState(1);
  const [cancelling, setCancelling] = useState(false);
  const [pausing, setPausing] = useState(false);    // WBS-9
  const [resuming, setResuming] = useState(false);   // WBS-9
  const [retrying, setRetrying] = useState(false);   // WBS-9

  // 拉取批次详情
  const fetchDetail = () => {
    const queryParams = new URLSearchParams();
    if (tasksStatus) queryParams.set("tasks_status", tasksStatus);
    queryParams.set("tasks_page", String(tasksPage));
    queryParams.set("tasks_page_size", "20");

    authenticatedFetch(`/api/batches/${batchId}?${queryParams.toString()}`)
      .then((r) => {
        if (r.status === 404) {
          router.push("/batches");
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then((d) => {
        if (d) setDetail(d);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (authState === "unauthenticated") {
      router.push(`/login?redirect=/batches/${batchId}`);
      return;
    }
    if (!batchId) return;
    fetchDetail();
  }, [authState, user, batchId]);

  // 轮询进度（运行中时每 5 秒）
  useEffect(() => {
    if (!detail) return;
    const terminalStatuses = ["COMPLETED", "FAILED", "CANCELLED", "PARTIAL"];
    if (terminalStatuses.includes(detail.status)) return;

    const timer = setInterval(() => {
      authenticatedFetch(`/api/batches/${batchId}/summary`)
        .then((r) => (r.ok ? r.json() : null))
        .then((summary: BatchSummary | null) => {
          if (summary) {
            setDetail((prev) =>
              prev
                ? {
                    ...prev,
                    status: summary.status,
                    completed_tasks: summary.completed_tasks,
                    failed_tasks: summary.failed_tasks,
                    cancelled_tasks: summary.cancelled_tasks,
                    paused_tasks: summary.paused_tasks,
                    running_tasks: summary.running_tasks,
                    partial_tasks: summary.partial_tasks,
                    paused: summary.paused,  // WBS-9
                  }
                : prev
            );
            // 如果批次已终止，重新拉取完整详情
            if (terminalStatuses.includes(summary.status)) {
              fetchDetail();
            }
          }
        })
        .catch(() => {});
    }, 5000);

    return () => clearInterval(timer);
  }, [detail?.status, batchId]);

  // 刷新子任务列表（状态/分页变化时）
  useEffect(() => {
    if (!batchId || !user) return;
    fetchDetail();
  }, [tasksStatus, tasksPage]);

  const handleCancel = async () => {
    if (!confirm("确定要取消此批次吗？所有未完成的任务将被标记为失败。")) return;
    setCancelling(true);
    try {
      const resp = await authenticatedFetch(`/api/batches/${batchId}/cancel`, {
        method: "POST",
      });
      if (!resp.ok) {
        const detail = await resp.json().then((d) => d.detail).catch(() => null);
        throw new Error(detail || "取消失败");
      }
      toastSuccess("批次取消已提交");
      fetchDetail();
    } catch (err) {
      toastError(err instanceof Error ? err.message : "取消失败");
    } finally {
      setCancelling(false);
    }
  };

  // WBS-9: 暂停批次
  const handlePause = async () => {
    setPausing(true);
    try {
      const resp = await authenticatedFetch(`/api/batches/${batchId}/pause`, {
        method: "POST",
      });
      if (!resp.ok) {
        const detail = await resp.json().then((d) => d.detail).catch(() => null);
        throw new Error(detail || "暂停失败");
      }
      toastSuccess("批次已暂停");
      fetchDetail();
    } catch (err) {
      toastError(err instanceof Error ? err.message : "暂停失败");
    } finally {
      setPausing(false);
    }
  };

  // WBS-9: 恢复批次
  const handleResume = async () => {
    setResuming(true);
    try {
      const resp = await authenticatedFetch(`/api/batches/${batchId}/resume`, {
        method: "POST",
      });
      if (!resp.ok) {
        const detail = await resp.json().then((d) => d.detail).catch(() => null);
        throw new Error(detail || "恢复失败");
      }
      toastSuccess("批次已恢复");
      fetchDetail();
    } catch (err) {
      toastError(err instanceof Error ? err.message : "恢复失败");
    } finally {
      setResuming(false);
    }
  };

  // WBS-9: 重跑失败任务
  const handleRetryFailed = async () => {
    if (!confirm("确定要重跑所有失败的任务吗？")) return;
    setRetrying(true);
    try {
      const resp = await authenticatedFetch(`/api/batches/${batchId}/retry-failed`, {
        method: "POST",
      });
      if (!resp.ok) {
        const detail = await resp.json().then((d) => d.detail).catch(() => null);
        throw new Error(detail || "重跑失败");
      }
      const result = await resp.json();
      toastSuccess(result.message || "重跑已提交");
      fetchDetail();
    } catch (err) {
      toastError(err instanceof Error ? err.message : "重跑失败");
    } finally {
      setRetrying(false);
    }
  };

  // WBS-9: 导出批次 CSV
  const handleExport = () => {
    const url = `/api/batches/${batchId}/export`;
    // 通过创建临时链接下载
    const a = document.createElement("a");
    a.href = url;
    a.download = `batch_${batchId}.csv`;
    // 添加认证头（通过 fetch 然后创建 blob URL）
    authenticatedFetch(url, {
      method: "POST",
    })
      .then((r) => r.blob())
      .then((blob) => {
        const blobUrl = URL.createObjectURL(blob);
        a.href = blobUrl;
        a.click();
        URL.revokeObjectURL(blobUrl);
        toastSuccess("导出完成");
      })
      .catch(() => toastError("导出失败"));
  };

  if (isLoading || authState === "unavailable" || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-neutral-500">加载中...</p>
      </main>
    );
  }

  if (loading && !detail) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-neutral-500">加载批次信息...</p>
      </main>
    );
  }

  if (!detail) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-neutral-500">批次不存在</p>
      </main>
    );
  }

  const terminalStatuses = ["COMPLETED", "FAILED", "CANCELLED", "PARTIAL"];
  const isTerminal = terminalStatuses.includes(detail.status);
  const runningCount = detail.running_tasks;
  const pausedCount = detail.paused_tasks;
  const partialCount = detail.partial_tasks;

  return (
    <PageShell>
      <button
        onClick={() => router.push("/batches")}
        className="mb-4 flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-950"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        返回批次列表
      </button>
      <PageHeader
        eyebrow="BATCH DETAIL"
        title={detail.name}
        meta={
          <div className="flex flex-wrap gap-2">
            <StatusBadge status={detail.status} label={statusLabel(detail.status)} />
            {detail.paused && <StatusBadge status="PARTIAL" label="已暂停" />}
          </div>
        }
        action={
          <>
              {/* WBS-9: 暂停/恢复 */}
              {detail.status === "RUNNING" && !detail.paused && (
                <Button variant="secondary" size="sm" isLoading={pausing} onClick={handlePause}>
                  暂停
                </Button>
              )}
              {detail.status === "RUNNING" && detail.paused && (
                <Button variant="secondary" size="sm" isLoading={resuming} onClick={handleResume}>
                  恢复
                </Button>
              )}
              {/* WBS-9: 重跑失败 */}
              {isTerminal && detail.failed_tasks > 0 && (
                <Button variant="secondary" size="sm" isLoading={retrying} onClick={handleRetryFailed}>
                  重跑失败 ({detail.failed_tasks})
                </Button>
              )}
              {/* WBS-9: 导出 */}
              {isTerminal && (
                <Button variant="secondary" size="sm" onClick={handleExport}>
                  导出 CSV
                </Button>
              )}
              {!isTerminal && (
                <Button variant="danger" size="sm" isLoading={cancelling} onClick={handleCancel}>
                  取消批次
                </Button>
              )}
          </>
        }
      />

        {/* 进度卡片 */}
        <Card variant="bordered" padding="lg">
          <h2 className="mb-4 text-sm font-medium text-neutral-700">执行进度</h2>

          <div className="mb-4 flex items-center gap-4">
            <div className="text-3xl font-bold text-neutral-950">
              {detail.completed_tasks}
              <span className="text-lg font-normal text-neutral-400"> / {detail.total_tasks}</span>
            </div>
            <div className="flex-1">
              {/* 分段进度条 */}
              <div className="flex h-3 w-full overflow-hidden rounded-full bg-neutral-950/10">
                {detail.total_tasks > 0 && (
                  <>
                    <span
                      className="h-full bg-green-500"
                      style={{ width: `${(detail.completed_tasks / detail.total_tasks) * 100}%` }}
                    />
                    <span
                      className="h-full bg-red-400"
                      style={{ width: `${(detail.failed_tasks / detail.total_tasks) * 100}%` }}
                    />
                    <span
                      className="h-full bg-gray-400"
                      style={{ width: `${(detail.cancelled_tasks / detail.total_tasks) * 100}%` }}
                    />
                    <span
                      className="h-full bg-cyan-400"
                      style={{ width: `${(runningCount / detail.total_tasks) * 100}%` }}
                    />
                  </>
                )}
              </div>
              {/* 图例 */}
              <div className="flex gap-4 mt-2 text-xs text-neutral-500">
                <span className="flex items-center gap-1">
                  <span className="h-2.5 w-2.5 rounded-sm bg-green-500"></span>
                  完成 {detail.completed_tasks}
                </span>
                <span className="flex items-center gap-1">
                  <span className="h-2.5 w-2.5 rounded-sm bg-red-400"></span>
                  失败 {detail.failed_tasks}
                </span>
                {detail.cancelled_tasks > 0 && (
                  <span className="flex items-center gap-1">
                    <span className="h-2.5 w-2.5 rounded-sm bg-gray-400"></span>
                    取消 {detail.cancelled_tasks}
                  </span>
                )}
                <span className="flex items-center gap-1">
                  <span className="h-2.5 w-2.5 rounded-sm bg-cyan-400"></span>
                  进行中 {Math.max(0, runningCount)}
                </span>
                {pausedCount > 0 && (
                  <span className="flex items-center gap-1">
                    <span className="h-2.5 w-2.5 rounded-sm bg-amber-400"></span>
                    已暂停 {pausedCount}
                  </span>
                )}
                {partialCount > 0 && (
                  <span className="flex items-center gap-1">
                    <span className="h-2.5 w-2.5 rounded-sm bg-orange-400"></span>
                    部分完成 {partialCount}
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-4 text-sm text-neutral-500">
            {detail.started_at && (
              <span>开始时间：{new Date(detail.started_at).toLocaleString("zh-CN")}</span>
            )}
            {detail.finished_at && (
              <span>完成时间：{new Date(detail.finished_at).toLocaleString("zh-CN")}</span>
            )}
            <span>研究工作流：{detail.root_skill_name}</span>
          </div>
        </Card>

        {(detail.research_mode === "OPPORTUNITY_DISCOVERY" || (detail.import_rows?.length ?? 0) > 0) && (
          <Card variant="bordered" padding="lg" className="mt-6">
            <BatchExecutionProgress
              rows={detail.import_rows ?? []}
              acceptedRows={detail.accepted_rows ?? detail.tasks_total}
              rejectedRows={detail.rejected_rows ?? 0}
            />
          </Card>
        )}

        {/* 子任务列表 */}
        <div className="mt-6">
          <Card variant="bordered" padding="lg">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
              <h2 className="text-sm font-medium text-neutral-700">
                任务列表
                <span className="text-neutral-400 font-normal ml-2">
                  共 {detail.tasks_total} 个
                </span>
              </h2>

              {/* 状态筛选 */}
              <SegmentedControl
                size="sm"
                options={TASK_STATUS_TABS}
                value={tasksStatus}
                onChange={(value) => { setTasksStatus(value); setTasksPage(1); }}
              />
            </div>

            {detail.tasks.length === 0 ? (
              <p className="text-sm text-neutral-500 py-4 text-center">暂无匹配任务</p>
            ) : (
              <div className="space-y-2">
                {detail.tasks.map((task) => (
                  <div
                    key={task.task_id}
                    onClick={() => router.push(`/tasks/${task.task_id}`)}
                    className="group flex cursor-pointer items-center justify-between rounded-lg border border-neutral-950/10 bg-white/70 p-3 transition-all hover:-translate-y-0.5 hover:border-neutral-950/30"
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-neutral-900 truncate">
                        {task.company_name}
                      </p>
                      <p className="text-xs text-neutral-500 truncate">
                        {task.demand_direction}
                      </p>
                    </div>
                    <div className="flex items-center gap-3 ml-4">
                      <div className="text-right">
                        <StatusBadge
                          status={task.observed_state}
                          label={
                            task.observed_state === "PAUSED"
                              ? "已暂停"
                              : task.observed_state === "PARTIAL"
                              ? "部分完成"
                              : statusLabel(task.status)
                          }
                        />
                        {task.observed_state === "PAUSED" && (
                          <p className="mt-1 text-xs text-amber-700">点击进入任务继续处理</p>
                        )}
                      </div>
                      <span className="text-xs text-neutral-400 whitespace-nowrap">
                        {new Date(task.created_at).toLocaleString("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                      <svg className="h-4 w-4 text-neutral-300 transition-transform group-hover:translate-x-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* 分页 */}
            {detail.tasks_total > detail.tasks_page_size && (
              <div className="flex items-center justify-center gap-4 pt-4 mt-4 border-t border-neutral-100">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={tasksPage <= 1}
                  onClick={() => setTasksPage(tasksPage - 1)}
                >
                  上一页
                </Button>
                <span className="text-sm text-neutral-500">
                  {tasksPage} / {Math.ceil(detail.tasks_total / detail.tasks_page_size)}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={tasksPage >= Math.ceil(detail.tasks_total / detail.tasks_page_size)}
                  onClick={() => setTasksPage(tasksPage + 1)}
                >
                  下一页
                </Button>
              </div>
            )}
          </Card>
        </div>
    </PageShell>
  );
}
