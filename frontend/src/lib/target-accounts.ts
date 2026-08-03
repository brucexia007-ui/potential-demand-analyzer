import { authenticatedFetch } from "@/lib/auth";

export type TargetAccount = {
  id: string;
  input_name: string;
  official_name: string | null;
  website: string | null;
  credit_code: string | null;
  industry: string | null;
  region: string | null;
  stock_code: string | null;
  parent_id: string | null;
  status: "UNRESOLVED" | "CONFIRMED" | "ARCHIVED";
};

export type TargetAccountInput = {
  input_name: string;
  official_name?: string;
  website?: string;
  credit_code?: string;
  industry?: string;
  region?: string;
  stock_code?: string;
};

export type WorkbenchProductMatch = {
  id: string;
  status: string;
  analysis_as_of_date: string;
  recommendation_score: number;
  evidence_confidence: number;
  information_completeness: number;
  missing_gate_layers: string[];
  revalidation_conditions: string[];
  matched_product_ids: string[];
  capability_gaps: string[];
  pending_verifications: string[];
  created_at: string;
};

export type WorkbenchTask = {
  id: string;
  demand_direction: string;
  status: string;
  observed_state: string;
  research_mode: string;
  created_at: string;
  updated_at: string;
  report_id: string | null;
  report_version_id: string | null;
  report_version_no: number | null;
  latest_product_match: WorkbenchProductMatch | null;
};

export type WorkbenchClaim = {
  id: string;
  task_id: string;
  report_version_id: string | null;
  claim_text: string;
  claim_type: string;
  opportunity_effect: string;
  status: string;
  confidence: number;
  evidence_count: number;
  last_verified_at: string | null;
  expires_at: string | null;
  updated_at: string;
};

export type WorkbenchGate = {
  id: string;
  task_id: string | null;
  decision: string;
  gate_level: string;
  analysis_as_of_date: string;
  summary: Record<string, unknown>;
  created_at: string;
};

export type WorkbenchCandidateProduct = {
  product_id: string;
  name: string;
  version_label: string;
  fit_score: number;
  rationale: string;
};

export type WorkbenchAction = {
  id: string;
  objective: string;
  target_role: string | null;
  recommended_channel: string | null;
  talking_point: string;
  suggested_questions: string[];
  expected_outcome: string;
  owner_user_id: string | null;
  due_at: string | null;
  status: string;
  result: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkbenchQualification = {
  id: string;
  assessment_no: number;
  framework_key: string;
  framework_version: string;
  gate_result: "INCOMPLETE" | "PASS" | "FAIL";
  score: number;
  information_completeness: number;
  hard_blockers: Array<Record<string, unknown>>;
  missing_fields: string[];
  summary: string;
  assessed_at: string;
};

export type WorkbenchOpportunity = {
  id: string;
  source_hypothesis_id: string;
  title: string;
  stage: string;
  owner_user_id: string;
  amount: string | null;
  currency: string | null;
  amount_source: string;
  probability: number;
  expected_close_date: string | null;
  closed_at: string | null;
  close_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkbenchHypothesis = {
  id: string;
  source_task_id: string | null;
  gate_decision_id: string;
  title: string;
  customer_problem_hypothesis: string;
  business_impact_hypothesis: string;
  trigger_event: string;
  counter_evidence_summary: string;
  hard_blockers: Record<string, unknown>[];
  status: string;
  confidence: number;
  information_completeness: number;
  owner_user_id: string | null;
  expires_at: string | null;
  supporting_claim_ids: string[];
  refuting_claim_ids: string[];
  latest_qualification: WorkbenchQualification | null;
  candidate_products: WorkbenchCandidateProduct[];
  actions: WorkbenchAction[];
  created_at: string;
  updated_at: string;
};

export type TargetAccountWorkbench = {
  account: TargetAccount & { created_at: string; updated_at: string };
  counts: {
    tasks: number;
    claims: number;
    gate_decisions: number;
    hypotheses: number;
    opportunities: number;
    pending_actions: number;
  };
  tasks: WorkbenchTask[];
  claims: WorkbenchClaim[];
  latest_gate: WorkbenchGate | null;
  hypotheses: WorkbenchHypothesis[];
  opportunities: WorkbenchOpportunity[];
};

type TargetAccountListResponse = { items: TargetAccount[] };
type TargetAccountCreateResponse = {
  created: boolean;
  account?: TargetAccount;
  candidates: TargetAccount[];
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function listTargetAccounts(): Promise<TargetAccount[]> {
  return (await requestJson<TargetAccountListResponse>("/api/target-accounts")).items;
}

export async function createTargetAccount(input: TargetAccountInput): Promise<TargetAccountCreateResponse> {
  return requestJson<TargetAccountCreateResponse>("/api/target-accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function getTargetAccountWorkbench(accountId: string): Promise<TargetAccountWorkbench> {
  return requestJson<TargetAccountWorkbench>(`/api/target-accounts/${accountId}/workbench`);
}
