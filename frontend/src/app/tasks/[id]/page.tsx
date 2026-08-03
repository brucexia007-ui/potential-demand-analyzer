"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell, StatusBadge } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";
import EvidencePanel from "@/components/evidence-panel";
import { ClaimAuditPanel, type AuditFindingsData } from "../../components/claim-audit-panel";
import { FieldAgentPanel } from "../../components/field-agent-panel";
import { TaskExecutionControls } from "../../components/task-execution-controls";
import { TaskExecutionStatus } from "../../components/task-execution-status";
import { ClarificationPanel } from "../../components/clarification-card";
import { ReportConversation } from "../../components/report-conversation";
import { ReportViewSwitcher } from "../../components/report-view-switcher";
import { ProductMatchPanel } from "../../components/product-match-panel";
import { ResearchDirectorPlanPanel } from "../../components/research-director-plan-panel";
import { getExecution, type ExecutionView } from "@/lib/task-execution";
import { useTaskEvents } from "@/lib/use-task-events";

type TaskDetail = {
  task_id: string;
  company_name: string;
  demand_direction: string;
  status: string;
  current_stage: string;
  progress: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  estimated_remaining_seconds?: number;
};

type TaskLog = {
  task_id: string;
  step_name: string;
  level: "INFO" | "WARNING" | "ERROR";
  message: string;
  created_at: string;
};

type ValidationResult = {
  passed: boolean;
  claims_total: number;
  claims_valid: number;
  violations: { claim_id: string; reason: string }[];
};

type TaskReport = {
  report_id: string;
  task_id: string;
  version_id: string;
  version_no: number;
  content_md: string;
  evidence_index: {
    count?: number;
    ids?: string[];
    claims?: { claim_id: string; evidence_ids: string[]; claim?: string }[];
    evidence_items?: Record<string, unknown>[];
    validation?: ValidationResult;
    audit?: AuditFindingsData;  // WBS-20b
  };
  created_at: string;
};

type TabType = "logs" | "report" | "evidences" | "audit" | "productMatch" | "fieldAgent";

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return s > 0 ? `${m} 分 ${s} 秒` : `${m} 分钟`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m > 0 ? `${h} 小时 ${m} 分` : `${h} 小时`;
}

export default function TaskDetailPage() {
  const params = useParams<{ id: string }>();
  const taskId = useMemo(() => String(params?.id ?? ""), [params]);

  const [task, setTask] = useState<TaskDetail | null>(null);
  const [logs, setLogs] = useState<TaskLog[]>([]);
  const [wsError, setWsError] = useState<string | null>(null);
  const [report, setReport] = useState<TaskReport | null>(null);
  const [isLoadingReport, setIsLoadingReport] = useState(false);
  const [activeTab, setActiveTab] = useState<TabType>("logs");
  const [isExporting, setIsExporting] = useState<"pdf" | "word" | null>(null);
  const [evFilterId, setEvFilterId] = useState<string | null>(null);
  const [displayProgress, setDisplayProgress] = useState(0);
  const [lastHeartbeat, setLastHeartbeat] = useState<number>(Date.now());
  const [heartbeatAgo, setHeartbeatAgo] = useState(0);
  const [execution, setExecution] = useState<ExecutionView | null>(null);
  const [clarificationRefreshToken, setClarificationRefreshToken] = useState(0);
  const { error: toastError } = useToast();
  const { lastSequence } = useTaskEvents(taskId);

  const MAX_REPORT_RETRIES = 3;
  const reportRetryCountRef = useRef(0);
  // 新执行状态机是任务运行状态的唯一事实来源。
  const effectiveStatus = execution?.observed_state ?? "PENDING";

  const refreshTaskSnapshot = useCallback(async (signal?: AbortSignal) => {
    if (!taskId) return;
    const request = (path: string) => authenticatedFetch(path, { signal })
      .then((response) => response.ok
        ? response.json()
        : Promise.reject(new Error(`HTTP ${response.status}`)));
    const [detailResult, logsResult, executionResult] = await Promise.allSettled([
      request(`/api/tasks/${taskId}`),
      request(`/api/tasks/${taskId}/logs`),
      getExecution(taskId),
    ]);
    if (signal?.aborted) return;

    if (detailResult.status === "fulfilled") {
      setTask(detailResult.value as TaskDetail);
      setWsError(null);
    } else {
      setWsError(detailResult.reason instanceof Error ? detailResult.reason.message : "任务详情加载失败");
    }
    if (logsResult.status === "fulfilled") {
      setLogs((logsResult.value as { logs?: TaskLog[] }).logs ?? []);
    }
    if (executionResult.status === "fulfilled") {
      setExecution(executionResult.value);
    }
    setClarificationRefreshToken((current) => current + 1);
  }, [taskId]);

  const refreshCurrentReport = async () => {
    const response = await authenticatedFetch(`/api/reports/${taskId}`);
    if (!response.ok) throw new Error(`刷新正式报告失败 (HTTP ${response.status})`);
    setReport(await response.json() as TaskReport);
  };

  useEffect(() => {
    if (!taskId) return;
    const controller = new AbortController();
    void refreshTaskSnapshot(controller.signal);
    return () => controller.abort();
  }, [taskId, refreshTaskSnapshot]);

  useEffect(() => {
    if (lastSequence > 0) void refreshTaskSnapshot();
  }, [lastSequence, refreshTaskSnapshot]);

  useEffect(() => {
    const terminalStates = new Set(["COMPLETED", "FAILED", "CANCELLED", "PARTIAL"]);
    if (!taskId || terminalStates.has(effectiveStatus)) return;
    const refresh = () => {
      if (document.visibilityState === "visible") void refreshTaskSnapshot();
    };
    const intervalId = window.setInterval(refresh, 10_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [effectiveStatus, refreshTaskSnapshot, taskId]);

  useEffect(() => {
    const id = setInterval(() => {
      setHeartbeatAgo(Math.floor((Date.now() - lastHeartbeat) / 1000));
    }, 2000);
    return () => clearInterval(id);
  }, [lastHeartbeat]);

  // 心跳计时器

  // 进度条反假死：平滑逼近 + 停滞时微小增量
  useEffect(() => {
    if (!task) return;
    const targetPct = task.progress;
    setLastHeartbeat(Date.now());

    const animate = () => {
      setDisplayProgress((prev) => {
        if (effectiveStatus !== "RUNNING") return targetPct;
        const gap = targetPct - prev;
        if (Math.abs(gap) < 0.3) {
          // 停滞时对数衰减微增（最大不超过目标 + 2%）
          return Math.min(targetPct + 2, prev + 0.08);
        }
        // 有真实进度时平滑追赶
        return prev + gap * 0.25;
      });
    };

    const id = setInterval(animate, 600);
    return () => clearInterval(id);
  }, [task?.progress, effectiveStatus]);

  // 获取当前阶段的显示进度
  const durableTotalUnits = execution?.dimensions.reduce((sum, item) => sum + item.total_units, 0) ?? 0;
  const durableCompletedUnits = execution?.dimensions.reduce((sum, item) => sum + item.completed_units, 0) ?? 0;
  const stageProgress = durableTotalUnits > 0 ? Math.round((durableCompletedUnits / durableTotalUnits) * 100) : 0;
  const showFakeHint = false;

  // 任务完成后自动拉取报告（含退避重试）
  useEffect(() => {
    if (effectiveStatus !== "COMPLETED" && effectiveStatus !== "PARTIAL") return;
    if (reportRetryCountRef.current >= MAX_REPORT_RETRIES) return;

    let cancelled = false;
    setIsLoadingReport(true);

    const fetchReport = () => {
      authenticatedFetch(`/api/reports/${taskId}`)
        .then((res) => {
          if (cancelled) return;
          if (!res.ok) {
            if (res.status === 404) {
              throw new Error("报告尚未生成或生成失败");
            }
            if (res.status === 429) {
              throw new Error("RATE_LIMITED");
            }
            throw new Error(`HTTP ${res.status}`);
          }
          return res.json();
        })
        .then((data: TaskReport | undefined) => {
          if (cancelled || !data) return;
          setReport(data);
          reportRetryCountRef.current = 0;
          setIsLoadingReport(false);
        })
        .catch((err) => {
          if (cancelled) return;
          const isRateLimited = err instanceof Error && err.message === "RATE_LIMITED";
          reportRetryCountRef.current += 1;

          if (isRateLimited && reportRetryCountRef.current < MAX_REPORT_RETRIES) {
            const delay = Math.pow(2, reportRetryCountRef.current) * 1000;
            setIsLoadingReport(false);
            setTimeout(() => {
              if (!cancelled) {
                setIsLoadingReport(true);
                fetchReport();
              }
            }, delay);
            return;
          }

          setIsLoadingReport(false);
          if (reportRetryCountRef.current >= MAX_REPORT_RETRIES) {
            toastError("报告加载失败，已重试多次，请刷新页面或稍后再试");
          } else {
            toastError(err instanceof Error ? err.message : "报告加载失败");
          }
        });
    };

    fetchReport();

    return () => {
      cancelled = true;
    };
  }, [effectiveStatus, taskId]);

  // 格式化时间显示
  const formatTime = (isoString: string) => {
    try {
      return new Date(isoString).toLocaleString("zh-CN");
    } catch {
      return isoString;
    }
  };

  const getStatusLabel = (status: string) => {
    const labels: Record<string, string> = {
      COMPLETED: "已完成",
      FAILED: "已失败",
      RUNNING: "执行中",
      PENDING: "等待中",
      PARTIAL: "部分完成",
      WAITING_FOR_INPUT: "等待确认",
      PAUSED: "已暂停",
      CANCELLED: "已取消",
    };
    return labels[status] || status;
  };

  // 导出 PDF
  const handleExportPdf = async () => {
    if (!taskId || isExporting) return;
    setIsExporting("pdf");
    try {
      const res = await authenticatedFetch(`/api/reports/${taskId}/pdf`);
      if (!res.ok) throw new Error("导出失败");
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report_${taskId}.pdf`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      toastError("PDF 导出失败，请重试");
    } finally {
      setIsExporting(null);
    }
  };

  // 导出 Word
  const handleExportWord = async () => {
    if (!taskId || isExporting) return;
    setIsExporting("word");
    try {
      const res = await authenticatedFetch(`/api/reports/${taskId}/docx`);
      if (!res.ok) throw new Error("导出失败");
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report_${taskId}.docx`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      toastError("Word 导出失败，请重试");
    } finally {
      setIsExporting(null);
    }
  };

  // 获取日志级别样式
  const getLevelStyle = (level: string) => {
    switch (level) {
      case "ERROR":
        return "text-red-700 bg-red-50";
      case "WARNING":
        return "text-amber-700 bg-amber-50";
      default:
        return "text-neutral-700 bg-neutral-50";
    }
  };

  return (
    <PageShell>
      <PageHeader
        eyebrow="TASK DETAIL"
        title="任务详情"
        description={<span className="font-mono">ID: {taskId}</span>}
      />

        {/* 任务状态卡片 */}
        <Card variant="bordered" padding="lg" className="mb-6">
          <div className="mb-6 flex items-center justify-between">
            <h2 className="text-lg font-medium text-neutral-950">任务状态</h2>
            <div className="flex items-center gap-3">
              {task && <StatusBadge status={effectiveStatus} label={getStatusLabel(effectiveStatus)} />}
              <TaskExecutionControls taskId={taskId} execution={execution} onChanged={refreshTaskSnapshot} onError={toastError} />
            </div>
          </div>
          <TaskExecutionStatus execution={execution} />
          <ClarificationPanel
            taskId={taskId}
            refreshToken={clarificationRefreshToken + lastSequence}
            onResolved={refreshTaskSnapshot}
            onError={toastError}
          />

          {task ? (
            <div className="space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <p className="text-sm text-neutral-500 mb-1">公司名称</p>
                  <p className="font-medium text-neutral-950">{task.company_name}</p>
                </div>
                <div>
                  <p className="text-sm text-neutral-500 mb-1">需求方向</p>
                  <p className="font-medium text-neutral-950">{task.demand_direction}</p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <p className="text-sm text-neutral-500 mb-1">当前阶段</p>
                  <p className="text-neutral-950">{task.current_stage || "-"}</p>
                </div>
                <div>
                  <p className="text-sm text-neutral-500 mb-1">创建时间</p>
                  <p className="text-neutral-950">{formatTime(task.created_at)}</p>
                </div>
              </div>

              {/* 进度条 */}
              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="text-neutral-600">
                    执行进度
                    {effectiveStatus === "RUNNING" && (
                      <span className="ml-2 text-xs text-neutral-400">
                        ({task.current_stage || "执行中"})
                      </span>
                    )}
                  </span>
                  <span className="font-medium text-neutral-950">{stageProgress}%</span>
                </div>
                <div className="relative h-2 w-full overflow-hidden rounded-full bg-neutral-950/10">
                  <div
                    className={`h-full rounded-full transition-all duration-300 ease-out ${
                      effectiveStatus === "FAILED" ? "bg-red-600" :
                      effectiveStatus === "COMPLETED" ? "bg-green-600" :
                      "bg-cyan-500"
                    }`}
                    style={{ width: `${stageProgress}%` }}
                  />
                  {showFakeHint && (
                    <div
                      className="absolute top-0 h-full animate-pulse rounded-full bg-cyan-400/30"
                      style={{
                        width: `${(displayProgress - (task.progress ?? 0)) * 3}%`,
                        left: `${(task.progress ?? 0)}%`,
                      }}
                    />
                  )}
                </div>
                <div className="flex justify-between items-center mt-2">
                  {effectiveStatus === "RUNNING" &&
                    task.estimated_remaining_seconds !== undefined &&
                    task.estimated_remaining_seconds >= 0 && (
                    <p className="text-xs text-neutral-500">
                      预估剩余：{formatDuration(task.estimated_remaining_seconds)}
                    </p>
                  )}
                  {effectiveStatus === "RUNNING" && (
                    <p className="text-xs text-neutral-400">
                      最后心跳：{heartbeatAgo}秒前
                    </p>
                  )}
                </div>
              </div>

              {task.error_message && (
                <div className="rounded-lg border border-red-200 bg-red-50 p-3">
                  <p className="text-sm text-red-700">错误：{task.error_message}</p>
                </div>
              )}
            </div>
          ) : (
            <p className="text-neutral-500">正在加载任务状态...</p>
          )}

          {wsError && (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-sm text-amber-700">{wsError}</p>
            </div>
          )}
        </Card>

        <Card variant="bordered" padding="lg" className="mb-6">
          <ResearchDirectorPlanPanel taskId={taskId} />
        </Card>

        {/* Tab 切换 - 任务完成或失败后显示 */}
        {(effectiveStatus === "COMPLETED" || effectiveStatus === "PARTIAL" || effectiveStatus === "FAILED") && (
          <Card variant="bordered" padding="none" className="mb-6">
            <div className="border-b border-neutral-950/10">
              <nav className="flex overflow-x-auto px-2">
                <button
                  onClick={() => setActiveTab("logs")}
                  className={`border-b-2 px-5 py-3 text-sm font-medium transition-colors ${
                    activeTab === "logs"
                      ? "border-neutral-950 text-neutral-950"
                      : "border-transparent text-neutral-500 hover:text-neutral-700"
                  }`}
                >
                  执行日志
                </button>
                <button
                  onClick={() => setActiveTab("report")}
                  className={`border-b-2 px-5 py-3 text-sm font-medium transition-colors ${
                    activeTab === "report"
                      ? "border-neutral-950 text-neutral-950"
                      : "border-transparent text-neutral-500 hover:text-neutral-700"
                  }`}
                >
                  分析报告
                </button>
                <button
                  onClick={() => setActiveTab("evidences")}
                  className={`border-b-2 px-5 py-3 text-sm font-medium transition-colors ${
                    activeTab === "evidences"
                      ? "border-neutral-950 text-neutral-950"
                      : "border-transparent text-neutral-500 hover:text-neutral-700"
                  }`}
                >
                  证据回溯
                </button>
                <button
                  onClick={() => setActiveTab("audit")}
                  className={`border-b-2 px-5 py-3 text-sm font-medium transition-colors ${
                    activeTab === "audit"
                      ? "border-neutral-950 text-neutral-950"
                      : "border-transparent text-neutral-500 hover:text-neutral-700"
                  }`}
                >
                  证据审计
                </button>
                <button
                  onClick={() => setActiveTab("productMatch")}
                  className={`border-b-2 px-5 py-3 text-sm font-medium transition-colors ${
                    activeTab === "productMatch"
                      ? "border-neutral-950 text-neutral-950"
                      : "border-transparent text-neutral-500 hover:text-neutral-700"
                  }`}
                >
                  产品匹配
                </button>
                <button
                  onClick={() => setActiveTab("fieldAgent")}
                  className={`border-b-2 px-5 py-3 text-sm font-medium transition-colors ${
                    activeTab === "fieldAgent"
                      ? "border-neutral-950 text-neutral-950"
                      : "border-transparent text-neutral-500 hover:text-neutral-700"
                  }`}
                >
                  体验式背调
                </button>
              </nav>
            </div>

            <div className="p-6">
              {/* 日志 Tab */}
              {activeTab === "logs" && (
                <div>
                  <h3 className="text-lg font-medium text-neutral-900 mb-4">阶段日志</h3>
                  {logs.length === 0 ? (
                    <p className="text-neutral-500 text-center py-8">暂无日志</p>
                  ) : (
                    <ul className="space-y-2 max-h-96 overflow-y-auto">
                      {logs.map((log, idx) => (
                        <li
                          key={`${log.created_at}-${idx}`}
                          className={`rounded-lg px-3 py-2 text-sm ${getLevelStyle(log.level)}`}
                        >
                          <span className="font-mono text-xs opacity-75 mr-2">
                            {formatTime(log.created_at)}
                          </span>
                          <span className="font-medium mr-2">[{log.level}]</span>
                          <span>{log.step_name} - {log.message}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {/* 报告 Tab */}
              {activeTab === "report" && (
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-medium text-neutral-950">分析报告</h3>
                    <div className="flex gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={handleExportPdf}
                        disabled={isLoadingReport || !!isExporting || !report}
                        title={!report ? "报告不可用" : "导出 PDF"}
                      >
                        {isExporting === "pdf" ? (
                          <span className="flex items-center gap-2">
                            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            导出中...
                          </span>
                        ) : (
                          "导出 PDF"
                        )}
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={handleExportWord}
                        disabled={isLoadingReport || !!isExporting || !report}
                        title={!report ? "报告不可用" : "导出 Word"}
                      >
                        {isExporting === "word" ? (
                          <span className="flex items-center gap-2">
                            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            导出中...
                          </span>
                        ) : (
                          "导出 Word"
                        )}
                      </Button>
                    </div>
                  </div>

                  {/* 证据校验横幅 */}
                  {report?.evidence_index?.validation && (
                    <div className={`mb-4 rounded-lg border p-4 ${
                      report.evidence_index.validation.passed
                        ? "bg-green-50 border-green-200"
                        : "bg-red-50 border-red-200"
                    }`}>
                      <div className="flex items-start gap-2">
                        <span className="text-xs font-semibold">
                          {report.evidence_index.validation.passed ? "OK" : "ERR"}
                        </span>
                        <div className="flex-1 min-w-0">
                          <p className={`text-sm font-medium ${
                            report.evidence_index.validation.passed ? "text-green-700" : "text-red-700"
                          }`}>
                            {report.evidence_index.validation.passed
                              ? `证据校验通过（${report.evidence_index.validation.claims_valid}/${report.evidence_index.validation.claims_total} 条 claims 有效）`
                              : `证据校验未通过（${report.evidence_index.validation.claims_valid}/${report.evidence_index.validation.claims_total} 条 claims 有效）`}
                          </p>
                          {!report.evidence_index.validation.passed && report.evidence_index.validation.violations.length > 0 && (
                            <details className="mt-2">
                              <summary className="text-xs text-red-600 cursor-pointer hover:text-red-800">
                                查看违规详情（{report.evidence_index.validation.violations.length} 项）
                              </summary>
                              <ul className="mt-2 space-y-1">
                                {report.evidence_index.validation.violations.map((v, i) => (
                                  <li key={i} className="text-xs text-red-600 pl-2 border-l-2 border-red-200">
                                    <span className="font-mono">{v.claim_id}</span>: {v.reason}
                                  </li>
                                ))}
                              </ul>
                            </details>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {isLoadingReport ? (
                    <div className="text-center py-12">
                      <div className="mb-4 inline-block h-6 w-6 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent"></div>
                      <p className="text-neutral-600">报告加载中...</p>
                    </div>
                  ) : report ? (
                    <div>
                    <ReportViewSwitcher
                      reportId={report.report_id}
                      versionId={report.version_id}
                      onEvidenceOpen={(evidenceId) => {
                        setEvFilterId(evidenceId);
                        setActiveTab("evidences");
                      }}
                      onError={toastError}
                    />
                    <ReportConversation
                      reportId={report.report_id}
                      onReportAccepted={refreshCurrentReport}
                      onError={toastError}
                    />
                    </div>
                  ) : effectiveStatus === "FAILED" ? (
                    <div className="text-center py-12">
                      <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-lg border border-red-200 bg-red-50 text-xs font-semibold text-red-700">ERR</div>
                      <p className="text-neutral-700 font-medium mb-2">任务执行失败，未生成报告</p>
                      {task?.error_message && (
                        <p className="text-sm text-neutral-500 max-w-md mx-auto">
                          {task?.error_message}
                        </p>
                      )}
                    </div>
                  ) : (
                    <div className="text-center py-12">
                      <p className="text-neutral-600">报告正在生成中，请稍候...</p>
                    </div>
                  )}
                </div>
              )}

              {/* 证据回溯 Tab */}
              {activeTab === "evidences" && (
                <div>
                  <EvidencePanel taskId={taskId} filterId={evFilterId} onFilterHandled={() => setEvFilterId(null)} />
                </div>
              )}

              {/* 证据审计 Tab (WBS-20b) */}
              {activeTab === "audit" && (
                <div>
                  <ClaimAuditPanel auditData={report?.evidence_index?.audit ?? null} />
                </div>
              )}

              {/* 需求—能力—缺口匹配 Tab */}
              {activeTab === "productMatch" && (
                <ProductMatchPanel taskId={taskId} />
              )}

              {/* 体验式背调 Tab (WBS-21b) */}
              {activeTab === "fieldAgent" && (
                <div>
                  <FieldAgentPanel taskId={taskId} />
                </div>
              )}
            </div>
          </Card>
        )}

        {/* Harness 执行可视化 - 运行中状态显示 */}
        {/* 运行中状态 - 显示日志预览 */}
        {effectiveStatus === "RUNNING" && (
          <Card variant="bordered" padding="lg">
            <h3 className="text-lg font-medium text-neutral-900 mb-4">实时日志</h3>
            {logs.length === 0 ? (
              <p className="text-neutral-500 text-center py-8">等待任务执行...</p>
            ) : (
              <ul className="space-y-2 max-h-64 overflow-y-auto">
                {logs.slice(-10).map((log, idx) => (
                  <li
                    key={`running-${log.created_at}-${idx}`}
                    className={`rounded-lg px-3 py-2 text-sm ${getLevelStyle(log.level)}`}
                  >
                    <span className="font-medium mr-2">[{log.level}]</span>
                    <span>{log.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
    </PageShell>
  );
}
