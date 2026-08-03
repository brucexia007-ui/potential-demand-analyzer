import { authenticatedFetch } from "@/lib/auth";

export type ReportIntent = "EXPLANATION" | "FOLLOW_UP_RESEARCH" | "REPORT_REVISION";

export type ReportThread = {
  id: string;
  report_id: string;
  bound_version_id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ReportMessage = {
  id: string;
  thread_id: string;
  role: "USER" | "ASSISTANT" | "SYSTEM";
  intent: string;
  content: string;
  created_at: string;
  delivery_status: "PERSISTED";
};

export type ReportQAResult = {
  status: "ANSWERED" | "NEEDS_INTENT_SELECTION" | "ROUTED" | "CONTEXT_ACTION_REQUIRED" | "DRAFT_CREATED";
  user_message_id: string;
  assistant_message_id: string | null;
  intent: ReportIntent | null;
  answer: string | null;
  citation_count: number;
  allowed_intents: ReportIntent[];
  context_action: "COMPACT_L1_L2" | "SPLIT_OR_CLARIFY" | null;
  context_reasons: string[];
  draft_id: string | null;
};

export type FollowUpPreview = {
  question: string;
  stage_names: string[];
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  estimated_total_tokens: number;
  estimated_external_call_lower_bound: number;
  requires_confirmation: boolean;
  confirmation_reasons: string[];
  runtime_cost_notice: string;
};

export type FollowUpStart = FollowUpPreview & {
  status: "STARTED" | "CONFIRMATION_REQUIRED";
  task_id: string | null;
  task_run_id: string | null;
  research_run_id: string | null;
  queued_unit_keys: string[];
  idempotent: boolean;
};

export type FollowUpEvidenceItem = {
  id: string;
  dimension: string;
  title: string;
  snippet: string;
  url: string;
  source_type: string;
  data_domain: "external" | "customer_private" | "internal";
  published_at: string | null;
  captured_at: string;
};

export type FollowUpResearchSummary = {
  research_run_id: string;
  task_id: string;
  task_run_id: string;
  run_type: "FOLLOW_UP";
  status: "PENDING" | "RUNNING" | "WAITING_FOR_INPUT" | "COMPLETED" | "FAILED" | "CANCELLED" | "PARTIAL";
  question: string;
  search_query_count: number;
  search_result_count: number;
  fetched_result_count: number;
  evidence_count: number;
  evidence_by_domain: Record<"external" | "customer_private" | "internal", number>;
  evidence_items: FollowUpEvidenceItem[];
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
};

export type ReportDraftChange = {
  id: string;
  kind: "REPLACE" | "DELETE" | "INSERT";
  base_start: number;
  base_end: number;
  before: string;
  after: string;
};

export type ReportDraft = {
  id: string;
  report_id: string;
  base_version_id: string;
  thread_id: string | null;
  research_run_id: string | null;
  proposed_content_md: string;
  proposed_raw_data: Record<string, unknown>;
  proposed_evidence_index: Record<string, unknown>;
  summary: string;
  change_set: ReportDraftChange[];
  decision: { action?: string; selected_change_ids?: string[] };
  status: "DRAFT" | "PARTIALLY_ACCEPTED" | "ACCEPTED" | "REJECTED" | "STALE";
  accepted_version_id: string | null;
  created_at: string;
  decided_at: string | null;
};

export type BusinessViewType = "EXECUTIVE_30S" | "ACCOUNT_BRIEF" | "OPPORTUNITY_CARD" | "DEEP_REPORT";

export type BusinessView = {
  view_type: BusinessViewType;
  report_id: string;
  version_id: string;
  version_no: number;
  title: string;
  content_md: string;
  sections: Array<{ key: string; title: string; content_md: string; source_ids: string[] }>;
  citation_count: number;
  source_manifest: Array<{ source_type: string; source_id: string }>;
  generated_by: "DETERMINISTIC_ASSET_PROJECTION";
};

async function readJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
    throw new Error(typeof detail === "string" ? detail : `报告工作台请求失败 (${response.status})`);
  }
  return body as T;
}

export async function listReportThreads(reportId: string): Promise<ReportThread[]> {
  const result = await readJson<{ items: ReportThread[] }>(
    await authenticatedFetch(`/api/reports/${reportId}/threads`),
  );
  return result.items;
}

export async function createReportThread(reportId: string, title: string): Promise<ReportThread> {
  return readJson<ReportThread>(
    await authenticatedFetch(`/api/reports/${reportId}/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }),
  );
}

export async function listReportMessages(threadId: string): Promise<ReportMessage[]> {
  const result = await readJson<{ items: ReportMessage[] }>(
    await authenticatedFetch(`/api/report-threads/${threadId}/messages`),
  );
  return result.items;
}

export async function askReportQuestion(
  threadId: string,
  question: string,
  intent: ReportIntent,
): Promise<ReportQAResult> {
  return readJson<ReportQAResult>(
    await authenticatedFetch(`/api/report-threads/${threadId}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        selected_intent: intent,
        idempotency_key: crypto.randomUUID(),
      }),
    }),
  );
}

export async function previewFollowUpResearch(
  threadId: string,
  question: string,
  idempotencyKey: string,
): Promise<FollowUpPreview> {
  return readJson<FollowUpPreview>(
    await authenticatedFetch(`/api/report-threads/${threadId}/follow-up/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, idempotency_key: idempotencyKey }),
    }),
  );
}

export async function startFollowUpResearch(
  threadId: string,
  question: string,
  idempotencyKey: string,
  confirmedHighCost: boolean,
): Promise<FollowUpStart> {
  const response = await authenticatedFetch(`/api/report-threads/${threadId}/follow-up`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      idempotency_key: idempotencyKey,
      confirmed_high_cost: confirmedHighCost,
    }),
  });
  const body = await response.json().catch(() => null);
  if (response.status === 409 && body?.status === "CONFIRMATION_REQUIRED") return body as FollowUpStart;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
    throw new Error(typeof detail === "string" ? detail : `补充研究启动失败 (${response.status})`);
  }
  return body as FollowUpStart;
}

export async function getFollowUpResearchSummary(
  researchRunId: string,
): Promise<FollowUpResearchSummary> {
  return readJson<FollowUpResearchSummary>(
    await authenticatedFetch(`/api/research-runs/${researchRunId}/summary`),
  );
}

export async function listThreadFollowUps(
  threadId: string,
): Promise<FollowUpResearchSummary[]> {
  const result = await readJson<{ items: FollowUpResearchSummary[] }>(
    await authenticatedFetch(`/api/report-threads/${threadId}/follow-ups`),
  );
  return result.items;
}

export async function listReportDrafts(reportId: string): Promise<ReportDraft[]> {
  const result = await readJson<{ items: ReportDraft[] }>(
    await authenticatedFetch(`/api/reports/${reportId}/drafts`),
  );
  return result.items;
}

export async function createFollowUpReportDraft(
  researchRunId: string,
): Promise<ReportDraft> {
  return readJson<ReportDraft>(
    await authenticatedFetch(`/api/research-runs/${researchRunId}/report-draft`, {
      method: "POST",
    }),
  );
}

export async function decideReportDraft(
  draftId: string,
  action: "ACCEPT_ALL" | "ACCEPT_SELECTED" | "REJECT",
  selectedChangeIds: string[] = [],
): Promise<ReportDraft> {
  return readJson<ReportDraft>(
    await authenticatedFetch(`/api/report-drafts/${draftId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, selected_change_ids: selectedChangeIds }),
    }),
  );
}

export async function getReportBusinessView(
  reportId: string,
  viewType: BusinessViewType,
): Promise<BusinessView> {
  return readJson<BusinessView>(
    await authenticatedFetch(`/api/reports/${reportId}/views/${viewType}`),
  );
}
