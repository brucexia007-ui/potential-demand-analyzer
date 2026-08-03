"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  submitExecutionCommand,
  type CommandType,
  type ExecutionView,
  TaskExecutionApiError,
} from "@/lib/task-execution";

type Props = {
  taskId: string;
  execution: ExecutionView | null;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
};

export function TaskExecutionControls({ taskId, execution, onChanged, onError }: Props) {
  const [submitting, setSubmitting] = useState<CommandType | null>(null);
  if (!execution) return null;
  const observed = execution.observed_state;
  const pauseEnabled = ["PENDING", "QUEUED", "RUNNING"].includes(observed);
  const resumeEnabled = ["PAUSED", "WAITING_FOR_INPUT"].includes(observed);
  const cancelEnabled = !["COMPLETED", "FAILED", "CANCELLED", "PARTIAL"].includes(observed);

  const submit = async (command: CommandType) => {
    if (command === "CANCEL" && !window.confirm("确认取消此任务？未完成的工作单元将不再发起新调用。")) return;
    setSubmitting(command);
    try {
      await submitExecutionCommand(taskId, command, {
        idempotencyKey: crypto.randomUUID(),
        expectedControlVersion: execution.control_version,
      });
      await onChanged();
    } catch (error) {
      if (error instanceof TaskExecutionApiError && error.status === 409) {
        await onChanged();
        onError("任务状态已变化，已刷新后请重试。");
      } else {
        onError(error instanceof Error ? error.message : "任务控制请求失败");
      }
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="flex flex-wrap gap-2">
      <Button size="sm" variant="secondary" disabled={!pauseEnabled || submitting !== null} isLoading={submitting === "PAUSE"} onClick={() => submit("PAUSE")}>暂停</Button>
      <Button size="sm" variant="secondary" disabled={!resumeEnabled || submitting !== null} isLoading={submitting === "RESUME"} onClick={() => submit("RESUME")}>继续</Button>
      <Button size="sm" variant="danger" disabled={!cancelEnabled || submitting !== null} isLoading={submitting === "CANCEL"} onClick={() => submit("CANCEL")}>取消</Button>
    </div>
  );
}
