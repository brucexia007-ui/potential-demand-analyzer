"use client";

import { useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  createFollowUpReportDraft,
  getFollowUpResearchSummary,
  type FollowUpResearchSummary,
} from "@/lib/report-workspace";
import { getExecution, type ExecutionView } from "@/lib/task-execution";
import { useTaskEvents } from "@/lib/use-task-events";

type Props = {
  initialSummary: FollowUpResearchSummary;
  onSummaryChange?: (summary: FollowUpResearchSummary) => void;
  onDraftCreated?: () => void;
  onError: (message: string) => void;
};

const TERMINAL_STATES = new Set(["COMPLETED", "FAILED", "CANCELLED", "PARTIAL"]);

function stateLabel(state: string): string {
  return {
    PENDING: "等待调度",
    QUEUED: "已进入队列",
    RUNNING: "研究进行中",
    PAUSING: "正在暂停",
    PAUSED: "已暂停",
    WAITING_FOR_INPUT: "等待用户澄清",
    RECOVERING: "正在恢复",
    CANCELLING: "正在取消",
    COMPLETED: "研究完成",
    FAILED: "研究失败",
    CANCELLED: "已取消",
    PARTIAL: "部分完成",
  }[state] ?? state;
}

function domainLabel(domain: string): string {
  return {
    external: "外部公开",
    customer_private: "客户私有",
    internal: "我方内部",
  }[domain] ?? domain;
}

export function FollowUpResearchStatus({ initialSummary, onSummaryChange, onDraftCreated, onError }: Props) {
  const [summary, setSummary] = useState(initialSummary);
  const [execution, setExecution] = useState<ExecutionView | null>(null);
  const [creatingDraft, setCreatingDraft] = useState(false);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);
  const observedState = execution?.observed_state ?? summary.status;
  const terminal = TERMINAL_STATES.has(observedState);
  const { lastSequence, connectionState } = useTaskEvents(initialSummary.task_id, 0, !terminal);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [nextExecution, nextSummary] = await Promise.all([
          getExecution(initialSummary.task_id),
          getFollowUpResearchSummary(initialSummary.research_run_id),
        ]);
        if (cancelled) return;
        setExecution(nextExecution);
        setSummary(nextSummary);
        onSummaryChange?.(nextSummary);
      } catch (error) {
        if (!cancelled) onError(error instanceof Error ? error.message : "加载补充研究进度失败");
      }
    };
    void refresh();
    return () => { cancelled = true; };
  }, [initialSummary.research_run_id, initialSummary.task_id, lastSequence]);

  const progress = useMemo(() => {
    if (!execution) return TERMINAL_STATES.has(observedState) ? 100 : 0;
    const total = execution.dimensions.reduce((sum, item) => sum + item.total_units, 0);
    const completed = execution.dimensions.reduce((sum, item) => sum + item.completed_units, 0);
    if (total === 0) return TERMINAL_STATES.has(observedState) ? 100 : 0;
    return Math.min(100, Math.round((completed / total) * 100));
  }, [execution, observedState]);

  const createDraft = async () => {
    if (creatingDraft) return;
    setCreatingDraft(true);
    setDraftNotice(null);
    try {
      await createFollowUpReportDraft(summary.research_run_id);
      setDraftNotice("修订草案已生成。原报告尚未改变，请在下方审阅完整 Diff。");
      onDraftCreated?.();
    } catch (error) {
      onError(error instanceof Error ? error.message : "生成补研修订草案失败");
    } finally {
      setCreatingDraft(false);
    }
  };

  return (
    <Card variant="bordered" padding="md" className="mt-4 bg-sky-50/70" data-testid="follow-up-research-status">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-[0.14em] text-sky-800">FOLLOW-UP RESEARCH</p>
          <h4 className="mt-1 text-sm font-semibold text-neutral-950">{summary.question}</h4>
          <p className="mt-1 text-xs text-neutral-600">
            子任务 {summary.task_id} · {stateLabel(observedState)}
          </p>
        </div>
        <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-sky-900">
          {progress}%
        </span>
      </div>

      <div className="mt-3 h-2 overflow-hidden rounded-full bg-sky-100" aria-label={`补充研究进度 ${progress}%`}>
        <div className="h-full rounded-full bg-sky-700 transition-all" style={{ width: `${progress}%` }} />
      </div>
      {!terminal && (
        <p className="mt-2 text-xs text-neutral-500">
          事件连接：{connectionState === "connected" ? "已连接" : connectionState === "reconnecting" ? "正在重连并补偿遗漏事件" : "正在连接"}
        </p>
      )}

      <div className="mt-4 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg bg-white p-3 text-xs text-neutral-700">搜索结果 <strong>{summary.search_result_count}</strong></div>
        <div className="rounded-lg bg-white p-3 text-xs text-neutral-700">已抓取来源 <strong>{summary.fetched_result_count}</strong></div>
        <div className="rounded-lg bg-white p-3 text-xs text-neutral-700">正式 Evidence <strong>{summary.evidence_count}</strong></div>
      </div>
      <p className="mt-2 text-xs text-neutral-500">搜索结果只是候选来源，只有完成提取与审计后才计入正式 Evidence。</p>

      {summary.evidence_items.length > 0 && (
        <div className="mt-4">
          <h5 className="text-sm font-semibold text-neutral-900">本次补研新增 Evidence</h5>
          <ul className="mt-2 space-y-2">
            {summary.evidence_items.map((item) => (
              <li key={item.id} className="rounded-lg border border-neutral-950/10 bg-white p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-neutral-500">
                  <span>{domainLabel(item.data_domain)}</span>
                  <span>{item.dimension}</span>
                  <span>{item.source_type}</span>
                </div>
                <p className="mt-1 text-sm font-medium text-neutral-950">{item.title}</p>
                <p className="mt-1 line-clamp-2 text-xs leading-5 text-neutral-600">{item.snippet}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {terminal && (
        <div className="mt-4 rounded-lg bg-white px-3 py-3 text-xs text-neutral-700">
          <p>补研结果不会自动覆盖原报告；只有生成修订草案、审阅 Diff 并确认后才会产生新版本。</p>
          {(observedState === "COMPLETED" || observedState === "PARTIAL") && (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button size="sm" isLoading={creatingDraft} onClick={() => void createDraft()}>
                生成修订草案
              </Button>
              {draftNotice && <span role="status" className="text-emerald-700">{draftNotice}</span>}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
