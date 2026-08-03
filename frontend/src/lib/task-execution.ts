import { authenticatedFetch } from "@/lib/auth";

export type DesiredState = "RUNNING" | "PAUSED" | "CANCELLED";
export type ObservedState =
  | "PENDING" | "QUEUED" | "RUNNING" | "PAUSING" | "PAUSED"
  | "WAITING_FOR_INPUT" | "RECOVERING" | "CANCELLING" | "COMPLETED"
  | "FAILED" | "CANCELLED" | "PARTIAL";
export type CommandType = "PAUSE" | "RESUME" | "CANCEL";

export type ExecutionEvent = {
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type ExecutionView = {
  task_id: string;
  desired_state: DesiredState;
  observed_state: ObservedState;
  control_version: number;
  active_run: { id: string; generation: number; status: string; started_at: string | null } | null;
  dimensions: Array<{
    dimension: string;
    total_units: number;
    completed_units: number;
    remaining_units: number;
    status_counts: Record<string, number>;
  }>;
  remaining_work_units: number;
  budget: {
    reserved_amount: number;
    settled_amount: number;
    refunded_amount: number;
    net_reserved_amount: number;
    currencies: string[];
    settlement_count: number;
    settled_token_count: number;
  };
  latest_heartbeat_at: string | null;
  latest_checkpoint: {
    stage_run_id: string;
    dimension: string;
    stage: string;
    checkpoint_version: number;
    persisted_at: string;
  } | null;
  recovery_count: number;
  eta: { p50_seconds: number; p90_seconds: number } | null;
};

export type CommandResult = {
  command_id: string;
  command_type: CommandType;
  applied: boolean;
  idempotent: boolean;
  desired_state: DesiredState | null;
  observed_state: ObservedState | null;
  control_version: number | null;
  run_id: string | null;
  reason: string | null;
};

export class TaskExecutionApiError extends Error {
  constructor(public readonly status: number, message: string, public readonly detail?: unknown) {
    super(message);
  }
}

async function readJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
    const message = typeof detail === "string" ? detail : `任务执行请求失败 (${response.status})`;
    throw new TaskExecutionApiError(response.status, message, detail);
  }
  return body as T;
}

export async function getExecution(taskId: string): Promise<ExecutionView> {
  return readJson<ExecutionView>(await authenticatedFetch(`/api/tasks/${taskId}/execution`));
}

export async function getExecutionEvents(taskId: string, afterSequence = 0): Promise<ExecutionEvent[]> {
  const query = new URLSearchParams({ after_sequence: String(afterSequence) });
  const response = await readJson<{ events: ExecutionEvent[] }>(
    await authenticatedFetch(`/api/tasks/${taskId}/execution/events?${query}`),
  );
  return response.events;
}

export async function submitExecutionCommand(
  taskId: string,
  command: CommandType,
  input: { idempotencyKey: string; expectedControlVersion: number },
): Promise<CommandResult> {
  return readJson<CommandResult>(await authenticatedFetch(`/api/tasks/${taskId}/${command.toLowerCase()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      idempotency_key: input.idempotencyKey,
      expected_control_version: input.expectedControlVersion,
    }),
  }));
}

export function executionEventStreamUrl(taskId: string, afterSequence: number): string {
  return `/api/tasks/${taskId}/execution/events/stream?after_sequence=${afterSequence}`;
}

export function parseExecutionSseFrame(frame: string): ExecutionEvent | null {
  const lines = frame.split("\n");
  const id = lines.find((line) => line.startsWith("id: "))?.slice(4);
  const eventType = lines.find((line) => line.startsWith("event: "))?.slice(7);
  const data = lines.find((line) => line.startsWith("data: "))?.slice(6);
  if (!id || !eventType || !data) return null;
  const parsed = JSON.parse(data) as ExecutionEvent;
  if (parsed.sequence !== Number(id) || parsed.event_type !== eventType) {
    throw new Error("任务事件 SSE 帧不一致");
  }
  return parsed;
}
