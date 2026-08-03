import { authenticatedFetch } from "@/lib/auth";

export type ClarificationOption = {
  code: string;
  label: string;
  impact: string;
};

export type ClarificationRequest = {
  id: string;
  task_id: string;
  phase: "PRE_EXECUTION" | "IN_EXECUTION" | "PRE_REPORT";
  category: string;
  materiality: "BLOCKING" | "MAJOR";
  question: string;
  options: ClarificationOption[];
  recommended_option: string | null;
  impact: string;
  status: "OPEN" | "ANSWERED" | "CANCELLED" | "SUPERSEDED";
  control_version: number;
};

export type ClarificationAnswerResult = {
  request_id: string;
  response_id: string;
  control_version: number;
  queued_stage_run_id: string | null;
  resumed: boolean;
  idempotent: boolean;
};

export type ClarificationCancelResult = {
  request_id: string;
  control_version: number;
  idempotent: boolean;
};

async function readJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
    throw new Error(typeof detail === "string" ? detail : `澄清请求失败 (${response.status})`);
  }
  return body as T;
}

export async function listTaskClarifications(taskId: string): Promise<ClarificationRequest[]> {
  return readJson<ClarificationRequest[]>(
    await authenticatedFetch(`/api/tasks/${taskId}/clarifications`),
  );
}

export async function answerClarification(
  request: ClarificationRequest,
  input: {
    answer?: string;
    selectedOption?: string;
    useRecommendedOption?: boolean;
    finalize?: boolean;
  },
): Promise<ClarificationAnswerResult> {
  return readJson<ClarificationAnswerResult>(
    await authenticatedFetch(`/api/clarifications/${request.id}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        answer: input.answer?.trim() || null,
        selected_option: input.selectedOption || null,
        use_recommended_option: input.useRecommendedOption ?? false,
        finalize: input.finalize ?? true,
        resume_idempotency_key: `clarification:${request.id}:${crypto.randomUUID()}`,
        expected_control_version: request.control_version,
      }),
    }),
  );
}

export async function cancelClarification(
  request: ClarificationRequest,
): Promise<ClarificationCancelResult> {
  return readJson<ClarificationCancelResult>(
    await authenticatedFetch(`/api/clarifications/${request.id}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        idempotency_key: `clarification-cancel:${request.id}:${crypto.randomUUID()}`,
        expected_control_version: request.control_version,
      }),
    }),
  );
}
