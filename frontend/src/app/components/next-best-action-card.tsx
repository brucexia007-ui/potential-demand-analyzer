"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import { applyActionCommand, type ActionCommand } from "@/lib/opportunities";
import type { WorkbenchAction } from "@/lib/target-accounts";


type Props = {
  action: WorkbenchAction;
  hypothesisStatus: string;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
};

const COMMANDS: Record<string, Array<{ value: ActionCommand; label: string }>> = {
  PENDING: [{ value: "START", label: "开始执行" }, { value: "CANCEL", label: "取消行动" }],
  IN_PROGRESS: [
    { value: "COMPLETE", label: "完成" },
    { value: "FAIL", label: "标记失败" },
    { value: "CANCEL", label: "取消行动" },
  ],
  FAILED: [{ value: "REOPEN", label: "重新打开" }, { value: "CANCEL", label: "取消行动" }],
};

const STATUS_LABEL: Record<string, string> = {
  PENDING: "待执行",
  IN_PROGRESS: "进行中",
  COMPLETED: "已完成",
  FAILED: "执行失败",
  CANCELLED: "已取消",
};

const date = (value: string | null) => value ? new Date(value).toLocaleDateString("zh-CN") : "未设置";
const inputDate = (value: string | null) => value ? new Date(value).toISOString().slice(0, 10) : "";

export function NextBestActionCard({ action, hypothesisStatus, onChanged, onError }: Props) {
  const [command, setCommand] = useState<ActionCommand | null>(null);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState("");
  const [dueDate, setDueDate] = useState(inputDate(action.due_at));
  const [submitting, setSubmitting] = useState(false);
  const commands = COMMANDS[action.status] ?? [];

  const submit = async () => {
    if (!command || !reason.trim()) {
      onError("请填写行动状态变更原因");
      return;
    }
    if ((command === "COMPLETE" || command === "FAIL") && !result.trim()) {
      onError("完成或失败时必须填写行动结果");
      return;
    }
    if ((command === "START" || command === "REOPEN") && !dueDate) {
      onError("开始或重开行动时必须设置截止日期");
      return;
    }
    setSubmitting(true);
    try {
      await applyActionCommand(action.id, {
        command,
        reason: reason.trim(),
        request_key: crypto.randomUUID(),
        ...((command === "COMPLETE" || command === "FAIL") ? { result: result.trim() } : {}),
        ...((command === "START" || command === "REOPEN") ? { due_at: new Date(`${dueDate}T23:59:59`).toISOString() } : {}),
      });
      setCommand(null);
      setReason("");
      setResult("");
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "行动状态更新失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4" data-testid="next-best-action">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-medium text-neutral-950">{action.objective}</p>
          <p className="mt-1 text-xs text-neutral-500">{action.target_role || "目标角色待确认"} · {action.recommended_channel || "渠道待确认"} · 截止 {date(action.due_at)}</p>
          {action.expected_outcome && <p className="mt-2 text-sm text-neutral-700">期望结果：{action.expected_outcome}</p>}
          {action.result && <p className="mt-2 rounded-md bg-white px-3 py-2 text-sm text-neutral-800">执行结果：{action.result}</p>}
        </div>
        <StatusBadge status={action.status} label={STATUS_LABEL[action.status] || action.status} />
      </div>

      {commands.length > 0 && (
        <div className="mt-3 border-t border-neutral-200 pt-3">
          {action.status === "PENDING" && hypothesisStatus !== "SALES_ACCEPTED" && hypothesisStatus !== "CUSTOMER_VALIDATED" ? (
            <p className="text-xs text-amber-700">销售接受该假设后才能开始行动。</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {commands.map((item) => (
                <button key={item.value} type="button" onClick={() => setCommand(item.value)} className="rounded-full border border-neutral-300 bg-white px-3 py-1.5 text-xs font-medium text-neutral-800">
                  {item.label}
                </button>
              ))}
            </div>
          )}
          {command && (
            <div className="mt-3 space-y-3 rounded-lg border border-neutral-200 bg-white p-3">
              <label className="block text-xs font-medium text-neutral-700">变更原因<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} maxLength={1000} className="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" /></label>
              {(command === "COMPLETE" || command === "FAIL") && <label className="block text-xs font-medium text-neutral-700">行动结果<textarea value={result} onChange={(event) => setResult(event.target.value)} rows={3} maxLength={4000} className="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" /></label>}
              {(command === "START" || command === "REOPEN") && <label className="block text-xs font-medium text-neutral-700">截止日期<input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} className="ml-3 rounded-lg border border-neutral-300 px-3 py-2 text-sm" /></label>}
              <div className="flex gap-2"><Button type="button" size="sm" isLoading={submitting} onClick={() => void submit()}>确认更新</Button><Button type="button" size="sm" variant="ghost" disabled={submitting} onClick={() => setCommand(null)}>取消</Button></div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
