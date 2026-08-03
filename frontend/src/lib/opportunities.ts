import { authenticatedFetch } from "@/lib/auth";


export type DiscoveryDepth = "quick" | "standard" | "deep";

export type DiscoveryPlanSnapshot = {
  research_mode: "OPPORTUNITY_DISCOVERY";
  target: {
    id: string;
    input_name: string;
    official_name: string | null;
    website: string | null;
    credit_code: string | null;
    industry: string | null;
    region: string | null;
    status: string;
  };
  capability_profile: {
    id: string;
    name: string;
    legal_entity_name: string | null;
    products: Array<{ id: string; name: string; version_label: string; product_line: string | null }>;
  };
  research_hypotheses: string[];
  skill: {
    root_name: string;
    version: string;
    description: string;
    execution_order: string[];
    research_dimensions: Array<{
      skill_name: string;
      description: string;
      questions: string[];
      sources: string[];
    }>;
    evaluation_skills: Array<{ skill_name: string; description: string }>;
  };
  scope: { demand_direction: string; depth: DiscoveryDepth };
  estimate: {
    external_calls: number;
    input_tokens: number;
    duration_minutes: { minimum: number; maximum: number };
    monetary_cost: { status: "UNAVAILABLE"; amount: null; currency: null; reason: string };
    basis: string;
  };
  confirmation: { required: boolean; reasons: string[] };
};

export type DiscoveryPlan = {
  id: string;
  status: "PREVIEWED" | "CONFIRMED" | "CONSUMED" | "EXPIRED";
  input_hash: string;
  requires_confirmation: boolean;
  expires_at: string;
  confirmed_at: string | null;
  snapshot: DiscoveryPlanSnapshot;
};

export type DiscoveryLaunchResult = {
  task_id: string;
  plan_id: string;
  status: string;
  execution_mode: "durable";
  created: boolean;
};

async function discoveryRequest<T>(url: string, init: RequestInit): Promise<T> {
  const response = await authenticatedFetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `自动发现请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function previewDiscoveryPlan(payload: {
  target_account_id: string;
  capability_profile_id: string;
  root_skill_name: string;
  demand_direction: string;
  depth: DiscoveryDepth;
}): Promise<DiscoveryPlan> {
  return discoveryRequest("/api/opportunities/discovery-plans/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function confirmDiscoveryPlan(planId: string): Promise<DiscoveryPlan> {
  return discoveryRequest(`/api/opportunities/discovery-plans/${planId}/confirm`, { method: "POST" });
}

export function launchDiscoveryPlan(planId: string): Promise<DiscoveryLaunchResult> {
  return discoveryRequest(`/api/opportunities/discovery-plans/${planId}/launch`, { method: "POST" });
}


export type HypothesisDecision =
  | "ACCEPT"
  | "REJECT"
  | "DEFER"
  | "REOPEN"
  | "CONFIRM_CUSTOMER"
  | "FAIL_VALIDATION"
  | "EXPIRE";

export type HypothesisDecisionPayload = {
  decision: HypothesisDecision;
  reason: string;
  request_key: string;
  deferred_until?: string;
  action_due_at?: string;
};

export type HypothesisDecisionResult = {
  hypothesis_id: string;
  status: string;
  owner_user_id: string | null;
  deferred_until: string | null;
  expires_at: string | null;
  transition: {
    id: string;
    from_status: string;
    to_status: string;
    reason: string;
    request_key: string;
    changed_by: string | null;
    created_at: string;
  };
  created: boolean;
};

export async function decideHypothesis(
  hypothesisId: string,
  payload: HypothesisDecisionPayload,
): Promise<HypothesisDecisionResult> {
  const response = await authenticatedFetch(`/api/opportunities/hypotheses/${hypothesisId}/decisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `商机假设裁决失败 (${response.status})`);
  }
  return response.json() as Promise<HypothesisDecisionResult>;
}

export type ActionCommand = "START" | "COMPLETE" | "FAIL" | "CANCEL" | "REOPEN";

export type ActionCommandPayload = {
  command: ActionCommand;
  reason: string;
  request_key: string;
  result?: string;
  due_at?: string;
};

export type ActionCommandResult = {
  action_id: string;
  status: string;
  owner_user_id: string | null;
  due_at: string | null;
  result: string | null;
  created: boolean;
};

export async function applyActionCommand(
  actionId: string,
  payload: ActionCommandPayload,
): Promise<ActionCommandResult> {
  const response = await authenticatedFetch(`/api/opportunities/actions/${actionId}/commands`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `行动状态更新失败 (${response.status})`);
  }
  return response.json() as Promise<ActionCommandResult>;
}

export type QualificationCriterionStatus =
  | "CUSTOMER_CONFIRMED"
  | "SUPPORTED"
  | "UNKNOWN"
  | "NEGATIVE";

export type QualificationFramework = {
  id: string;
  workspace_id: string;
  framework_key: string;
  version_no: number;
  name: string;
  methodology: "CUSTOM" | "MEDDPICC" | "BANT" | "SPICED" | "HYBRID";
  criteria: Array<{
    key: string;
    label: string;
    weight: number;
    required: boolean;
  }>;
  hard_blocker_rules: Array<{
    criterion_key: string;
    code: string;
    message: string;
    when_status: QualificationCriterionStatus;
  }>;
  minimum_score: number;
  minimum_completeness: number;
  status: string;
  created_by: string;
  published_at: string | null;
  created_at: string;
};

export type QualificationCard = {
  id: string;
  workspace_id: string;
  hypothesis_id: string;
  framework_id: string;
  assessment_no: number;
  framework_key: string;
  framework_version: string;
  criteria: Array<{
    key: string;
    label: string;
    weight: number;
    required: boolean;
    status: QualificationCriterionStatus;
    score_factor: number;
    claim_ids: string[];
    note: string;
  }>;
  hard_blockers: Array<Record<string, unknown>>;
  missing_fields: string[];
  gate_result: "INCOMPLETE" | "PASS" | "FAIL";
  score: number;
  information_completeness: number;
  summary: string;
  assessed_by: string;
  assessed_at: string;
  created_at: string;
};

export type QualificationAssessmentPayload = {
  framework_id: string;
  criteria: Array<{
    criterion_key: string;
    status: QualificationCriterionStatus;
    claim_ids?: string[];
    note?: string;
  }>;
  summary?: string;
};

export type FormalOpportunity = {
  id: string;
  workspace_id: string;
  target_account_id: string;
  source_hypothesis_id: string;
  title: string;
  stage: OpportunityStage;
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

export type OpportunityStage =
  | "QUALIFICATION"
  | "DISCOVERY"
  | "SOLUTION_SHAPING"
  | "PROPOSAL"
  | "TENDER"
  | "NEGOTIATION"
  | "WON"
  | "LOST"
  | "CANCELLED";

export type OpportunityStageHistory = {
  id: string;
  from_stage: OpportunityStage | null;
  to_stage: OpportunityStage;
  reason: string;
  request_key: string;
  changed_by: string | null;
  created_at: string;
};

async function opportunityRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `商机请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function listQualificationFrameworks(): Promise<QualificationFramework[]> {
  const response = await opportunityRequest<{ items: QualificationFramework[] }>(
    "/api/opportunities/qualification-frameworks",
  );
  return response.items;
}

export async function assessHypothesisQualification(
  hypothesisId: string,
  payload: QualificationAssessmentPayload,
): Promise<{ card: QualificationCard; created: boolean }> {
  return opportunityRequest(`/api/opportunities/hypotheses/${hypothesisId}/qualification-assessments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function convertHypothesis(
  hypothesisId: string,
  payload: {
    reason: string;
    request_key: string;
    title?: string;
    amount?: string;
    currency?: string;
    amount_source?: "UNSPECIFIED" | "CUSTOMER_CONFIRMED" | "USER_ESTIMATE" | "CRM_IMPORTED";
    probability?: number;
    expected_close_date?: string;
  },
): Promise<{ opportunity: FormalOpportunity; transition: OpportunityStageHistory; created: boolean }> {
  return opportunityRequest(`/api/opportunities/hypotheses/${hypothesisId}/convert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function changeOpportunityStage(
  opportunityId: string,
  payload: {
    to_stage: OpportunityStage;
    reason: string;
    request_key: string;
    close_reason?: string;
  },
): Promise<{ opportunity: FormalOpportunity; transition: OpportunityStageHistory; created: boolean }> {
  return opportunityRequest(`/api/opportunities/${opportunityId}/stages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listOpportunityHistory(opportunityId: string): Promise<OpportunityStageHistory[]> {
  const response = await opportunityRequest<{ items: OpportunityStageHistory[] }>(
    `/api/opportunities/${opportunityId}/history`,
  );
  return response.items;
}

export function getFormalOpportunity(opportunityId: string): Promise<FormalOpportunity> {
  return opportunityRequest(`/api/opportunities/${opportunityId}`);
}

export type FeedbackType =
  | "SIGNAL_ACCEPTED"
  | "SIGNAL_REJECTED"
  | "CUSTOMER_VALIDATED"
  | "CUSTOMER_INVALIDATED"
  | "STAGE_ADVANCED"
  | "WON"
  | "LOST"
  | "NO_OPPORTUNITY"
  | "IDENTIFICATION_ERROR";

export type WinLossReason = {
  id: string;
  code: string;
  label: string;
  description: string | null;
  category: "WIN" | "LOSS" | "NO_OPPORTUNITY" | "IDENTIFICATION_ERROR";
  active: boolean;
  sort_order: number;
  created_at: string;
};

export type BusinessFeedback = {
  id: string;
  target_account_id: string;
  hypothesis_id: string | null;
  opportunity_id: string | null;
  task_id: string | null;
  reason_id: string | null;
  feedback_type: FeedbackType;
  outcome_data: Record<string, unknown>;
  notes: string | null;
  effective_at: string;
  recorded_by: string;
  request_key: string;
  created_at: string;
};

export async function listWinLossReasons(category?: WinLossReason["category"]): Promise<WinLossReason[]> {
  const query = category ? `?category=${category}` : "";
  const response = await opportunityRequest<{ items: WinLossReason[] }>(
    `/api/watchlist/feedback/reasons${query}`,
  );
  return response.items;
}

export function createWinLossReason(payload: {
  code: string;
  label: string;
  description?: string;
  category: WinLossReason["category"];
  sort_order?: number;
}): Promise<WinLossReason> {
  return opportunityRequest("/api/watchlist/feedback/reasons", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listBusinessFeedback(targetAccountId: string): Promise<BusinessFeedback[]> {
  const response = await opportunityRequest<{ items: BusinessFeedback[] }>(
    `/api/watchlist/feedback?target_account_id=${encodeURIComponent(targetAccountId)}`,
  );
  return response.items;
}

export function recordBusinessFeedback(payload: {
  target_account_id: string;
  hypothesis_id?: string;
  opportunity_id?: string;
  task_id?: string;
  reason_id?: string;
  feedback_type: FeedbackType;
  outcome?: Record<string, unknown>;
  notes?: string;
  effective_at: string;
  request_key: string;
}): Promise<{ feedback: BusinessFeedback; created: boolean }> {
  return opportunityRequest("/api/watchlist/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export type OpportunityStakeholder = {
  id: string;
  workspace_id: string;
  target_account_id: string;
  opportunity_id: string | null;
  role_type: string;
  full_name: string | null;
  role_title: string | null;
  department: string | null;
  influence: "UNKNOWN" | "LOW" | "MEDIUM" | "HIGH";
  attitude: "UNKNOWN" | "SUPPORTIVE" | "NEUTRAL" | "OPPOSED";
  goals: string;
  concerns: string;
  relationship_strength: "UNKNOWN" | "NONE" | "WEAK" | "MEDIUM" | "STRONG";
  truth_status: "PUBLIC_INFERENCE" | "SALES_JUDGMENT" | "CUSTOMER_CONFIRMED";
  source_claim_id: string | null;
  communication_strategy: string;
  status: "ACTIVE" | "ARCHIVED";
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

export type OpportunityStakeholderPayload = {
  role_type: string;
  truth_status: "PUBLIC_INFERENCE" | "SALES_JUDGMENT" | "CUSTOMER_CONFIRMED";
  opportunity_id?: string;
  full_name?: string;
  role_title?: string;
  department?: string;
  influence?: "UNKNOWN" | "LOW" | "MEDIUM" | "HIGH";
  attitude?: "UNKNOWN" | "SUPPORTIVE" | "NEUTRAL" | "OPPOSED";
  goals?: string;
  concerns?: string;
  relationship_strength?: "UNKNOWN" | "NONE" | "WEAK" | "MEDIUM" | "STRONG";
  source_claim_id?: string;
  communication_strategy?: string;
};

export async function listOpportunityStakeholders(accountId: string): Promise<OpportunityStakeholder[]> {
  const response = await opportunityRequest<{ items: OpportunityStakeholder[] }>(
    `/api/opportunities/target-accounts/${accountId}/stakeholders`,
  );
  return response.items;
}

export async function createOpportunityStakeholder(
  accountId: string,
  payload: OpportunityStakeholderPayload,
): Promise<OpportunityStakeholder> {
  return opportunityRequest(`/api/opportunities/target-accounts/${accountId}/stakeholders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateOpportunityStakeholder(
  stakeholderId: string,
  payload: OpportunityStakeholderPayload,
): Promise<OpportunityStakeholder> {
  return opportunityRequest(`/api/opportunities/stakeholders/${stakeholderId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function archiveOpportunityStakeholder(stakeholderId: string): Promise<OpportunityStakeholder> {
  return opportunityRequest(`/api/opportunities/stakeholders/${stakeholderId}`, { method: "DELETE" });
}

export type OpportunityCompetitor = {
  id: string;
  workspace_id: string;
  opportunity_id: string;
  competitor_type: "COMMERCIAL_VENDOR" | "INCUMBENT_VENDOR" | "CUSTOMER_SELF_BUILD" | "STATUS_QUO" | "DELAY" | "NO_INVESTMENT";
  name: string | null;
  truth_status: "PUBLIC_EVIDENCE" | "SALES_JUDGMENT" | "CUSTOMER_CONFIRMED";
  source_claim_id: string | null;
  status: "ACTIVE" | "DISMISSED";
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

export type CompetitiveBattlecard = {
  id: string;
  workspace_id: string;
  competitor_id: string;
  version_no: number;
  current_contract: Record<string, unknown>;
  switching_cost_assessment: string;
  competitor_strengths: Array<Record<string, unknown>>;
  competitor_weaknesses: Array<Record<string, unknown>>;
  our_differentiators: Array<Record<string, unknown>>;
  customer_decision_criteria: Array<Record<string, unknown>>;
  must_win_metrics: Array<Record<string, unknown>>;
  our_risks: Array<Record<string, unknown>>;
  prohibited_commitments: string[];
  discovery_questions: string[];
  ecosystem_partners: Array<Record<string, unknown>>;
  created_by: string;
  created_at: string;
};

export type BattlecardEvidenceItemPayload = {
  text: string;
  source_domain: "external" | "customer_private" | "internal";
  source_id: string;
};

export type CompetitiveBattlecardPayload = {
  current_contract?: { status: string; summary?: string; source_claim_ids?: string[] };
  switching_cost_assessment?: string;
  competitor_strengths?: BattlecardEvidenceItemPayload[];
  competitor_weaknesses?: BattlecardEvidenceItemPayload[];
  our_differentiators?: BattlecardEvidenceItemPayload[];
  customer_decision_criteria?: BattlecardEvidenceItemPayload[];
  must_win_metrics?: BattlecardEvidenceItemPayload[];
  our_risks?: BattlecardEvidenceItemPayload[];
  prohibited_commitments?: string[];
  discovery_questions?: string[];
  ecosystem_partners?: BattlecardEvidenceItemPayload[];
};

export type CompetitiveBattlecardDraft = {
  summary: string;
  battlecard: CompetitiveBattlecardPayload;
  uncertainties: string[];
  model: string | null;
  provider: string | null;
  usage: Record<string, number> | null;
};

export type ValueHypothesis = {
  id: string;
  workspace_id: string;
  opportunity_id: string;
  version_no: number;
  status: "NEEDS_VALIDATION" | "CUSTOMER_CONFIRMED" | "REJECTED";
  currency: string | null;
  time_horizon_months: number | null;
  inputs: Array<Record<string, unknown>>;
  formulas: Array<Record<string, unknown>>;
  outputs: Array<{ key: string; label: string; value: string | null; unit: string; is_complete: boolean }>;
  sensitivity_scenarios: Array<Record<string, unknown>>;
  assumptions: Array<Record<string, unknown>>;
  missing_parameters: string[];
  created_by: string;
  created_at: string;
};

export async function listOpportunityCompetitors(opportunityId: string): Promise<OpportunityCompetitor[]> {
  const response = await opportunityRequest<{ items: OpportunityCompetitor[] }>(
    `/api/opportunities/${opportunityId}/competitors`,
  );
  return response.items;
}

export function createOpportunityCompetitor(
  opportunityId: string,
  payload: {
    competitor_type: OpportunityCompetitor["competitor_type"];
    truth_status: OpportunityCompetitor["truth_status"];
    name?: string;
    source_claim_id?: string;
  },
): Promise<OpportunityCompetitor> {
  return opportunityRequest(`/api/opportunities/${opportunityId}/competitors`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function dismissOpportunityCompetitor(competitorId: string): Promise<OpportunityCompetitor> {
  return opportunityRequest(`/api/opportunities/competitors/${competitorId}`, { method: "DELETE" });
}

export async function listCompetitiveBattlecards(competitorId: string): Promise<CompetitiveBattlecard[]> {
  const response = await opportunityRequest<{ items: CompetitiveBattlecard[] }>(
    `/api/opportunities/competitors/${competitorId}/battlecards`,
  );
  return response.items;
}

export function createCompetitiveBattlecard(
  competitorId: string,
  payload: CompetitiveBattlecardPayload,
): Promise<{ battlecard: CompetitiveBattlecard; created: boolean }> {
  return opportunityRequest(`/api/opportunities/competitors/${competitorId}/battlecards`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function proposeCompetitiveBattlecardDraft(
  competitorId: string,
  payload: { claim_ids: string[]; internal_document_ids: string[]; model?: string },
): Promise<CompetitiveBattlecardDraft> {
  return opportunityRequest(`/api/opportunities/competitors/${competitorId}/battlecard-drafts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listValueHypotheses(opportunityId: string): Promise<ValueHypothesis[]> {
  const response = await opportunityRequest<{ items: ValueHypothesis[] }>(
    `/api/opportunities/${opportunityId}/value-hypotheses`,
  );
  return response.items;
}

export function calculateValueHypothesis(
  opportunityId: string,
  payload: Record<string, unknown>,
): Promise<{ hypothesis: ValueHypothesis; created: boolean }> {
  return opportunityRequest(`/api/opportunities/${opportunityId}/value-hypotheses`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
