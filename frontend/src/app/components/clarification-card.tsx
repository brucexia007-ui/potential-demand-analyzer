"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  answerClarification,
  cancelClarification,
  listTaskClarifications,
  type ClarificationRequest,
} from "@/lib/clarifications";

type ClarificationPanelProps = {
  taskId: string;
  refreshToken?: number;
  onResolved?: () => void | Promise<void>;
  onError: (message: string) => void;
};

function phaseLabel(phase: ClarificationRequest["phase"]): string {
  return {
    PRE_EXECUTION: "执行前确认",
    IN_EXECUTION: "研究中确认",
    PRE_REPORT: "报告前确认",
  }[phase];
}

function ClarificationItem({
  request,
  onCompleted,
  onError,
}: {
  request: ClarificationRequest;
  onCompleted: () => void | Promise<void>;
  onError: (message: string) => void;
}) {
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  const submit = async (input: {
    answer?: string;
    selectedOption?: string;
    useRecommendedOption?: boolean;
    finalize?: boolean;
  }) => {
    const submissionKey = input.finalize === false
      ? "PARTIAL"
      : input.useRecommendedOption
        ? "ASSUMPTION"
        : input.selectedOption ?? "FINAL";
    setSubmitting(submissionKey);
    setSavedMessage(null);
    try {
      const result = await answerClarification(request, input);
      if (!result.resumed) {
        setAnswer("");
        setSavedMessage("补充说明已保存，任务仍保持暂停；你可以继续补充，或明确确认后恢复研究。");
      }
      await onCompleted();
    } catch (error) {
      onError(error instanceof Error ? error.message : "提交澄清回答失败");
    } finally {
      setSubmitting(null);
    }
  };

  const recommendedOption = request.options.find(
    (option) => option.code === request.recommended_option,
  );

  const cancel = async () => {
    if (!window.confirm("取消本次研究任务？已完成的研究资产会保留，但任务不会继续生成报告。")) return;
    setSubmitting("CANCEL");
    try {
      await cancelClarification(request);
      await onCompleted();
    } catch (error) {
      onError(error instanceof Error ? error.message : "取消任务失败");
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <Card variant="bordered" padding="md" className="border-amber-300 bg-amber-50/90">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-amber-950 px-2.5 py-1 text-xs font-medium text-white">
          等待你的确认
        </span>
        <span className="text-xs font-medium text-amber-900">{phaseLabel(request.phase)}</span>
        <span className="text-xs text-amber-800">{request.category}</span>
      </div>

      <h3 className="mt-4 text-base font-semibold text-neutral-950">{request.question}</h3>
      <p className="mt-2 text-sm leading-6 text-neutral-700">为什么需要确认：{request.impact}</p>

      {request.options.length > 0 && (
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {request.options.map((option) => {
            const recommended = option.code === request.recommended_option;
            return (
              <button
                key={option.code}
                type="button"
                disabled={submitting !== null}
                onClick={() => void submit({
                  selectedOption: option.code,
                  useRecommendedOption: recommended,
                })}
                className={`rounded-xl border p-4 text-left transition disabled:opacity-50 ${
                  recommended
                    ? "border-neutral-950 bg-white shadow-sm"
                    : "border-neutral-950/15 bg-white/70 hover:border-neutral-950/40"
                }`}
              >
                <span className="flex items-center justify-between gap-3 text-sm font-semibold text-neutral-950">
                  {option.label}
                  {recommended && <span className="text-xs text-emerald-700">推荐</span>}
                </span>
                <span className="mt-2 block text-xs leading-5 text-neutral-600">{option.impact}</span>
              </button>
            );
          })}
        </div>
      )}

      <div className="mt-4">
        <label htmlFor={`clarification-${request.id}`} className="mb-2 block text-sm font-medium text-neutral-800">
          或补充你的实际情况
        </label>
        <textarea
          id={`clarification-${request.id}`}
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          maxLength={2000}
          rows={3}
          placeholder="请输入需要智能体采用的事实、边界或判断……"
          className="w-full rounded-xl border border-neutral-950/20 bg-white px-4 py-3 text-sm text-neutral-950 outline-none focus:border-neutral-950 focus:ring-2 focus:ring-neutral-950/10"
        />
      </div>

      {savedMessage && (
        <p role="status" className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {savedMessage}
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-3">
        <Button
          type="button"
          size="sm"
          disabled={!answer.trim() || submitting !== null}
          variant="ghost"
          isLoading={submitting === "PARTIAL"}
          onClick={() => void submit({ answer, finalize: false })}
        >
          保存说明，暂不继续
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={!answer.trim() || submitting !== null}
          isLoading={submitting === "FINAL"}
          onClick={() => void submit({ answer, finalize: true })}
        >
          提交完整回答并继续
        </Button>
        {recommendedOption && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={submitting !== null}
            isLoading={submitting === "ASSUMPTION"}
            onClick={() => void submit({ useRecommendedOption: true, finalize: true })}
          >
            按推荐假设继续：{recommendedOption.label}
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={submitting !== null}
          isLoading={submitting === "CANCEL"}
          onClick={() => void cancel()}
        >
          取消任务
        </Button>
      </div>
    </Card>
  );
}

export function ClarificationPanel({ taskId, refreshToken = 0, onResolved, onError }: ClarificationPanelProps) {
  const [requests, setRequests] = useState<ClarificationRequest[]>([]);

  const refresh = async () => {
    try {
      const items = await listTaskClarifications(taskId);
      setRequests(items.filter((item) => item.status === "OPEN"));
    } catch (error) {
      onError(error instanceof Error ? error.message : "加载澄清请求失败");
    }
  };

  useEffect(() => {
    if (taskId) void refresh();
    // refreshToken 来自 durable 事件序号，每次状态变化后刷新持久化澄清账本。
  }, [taskId, refreshToken]);

  if (requests.length === 0) return null;

  return (
    <section className="mt-5 space-y-4" aria-label="待确认事项">
      {requests.map((request) => (
        <ClarificationItem
          key={request.id}
          request={request}
          onError={onError}
          onCompleted={async () => {
            await refresh();
            await onResolved?.();
          }}
        />
      ))}
    </section>
  );
}
