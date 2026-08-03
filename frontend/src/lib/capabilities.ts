import { authenticatedFetch } from "@/lib/auth";

export type CapabilityProfile = {
  id: string;
  workspace_id: string;
  name: string;
  legal_entity_name: string | null;
  description: string;
  is_default: boolean;
  status: "ACTIVE" | "ARCHIVED";
  created_at: string;
  updated_at: string;
};

export type CapabilityProduct = {
  id: string;
  workspace_id: string;
  profile_id: string;
  name: string;
  product_line: string | null;
  version_label: string;
  summary: string;
  capabilities: Array<Record<string, unknown>>;
  constraints: Array<Record<string, unknown>>;
  unsuitable_scenarios: Array<Record<string, unknown>>;
  differentiators: Array<Record<string, unknown>>;
  supported_regions: string[];
  supported_industries: string[];
  status: "DRAFT" | "ACTIVE" | "ARCHIVED";
  effective_from: string | null;
  effective_to: string | null;
  created_at: string;
  updated_at: string;
};

export type CreateCapabilityProfilePayload = {
  name: string;
  legal_entity_name?: string;
  description?: string;
  is_default?: boolean;
};

export type CreateCapabilityProductPayload = {
  name: string;
  version_label: string;
  summary: string;
  product_line?: string;
  capabilities: Array<{ name: string }>;
  constraints: Array<{ name: string }>;
  unsuitable_scenarios: Array<{ name: string }>;
  differentiators: Array<{ name: string }>;
  supported_regions: string[];
  supported_industries: string[];
  status: "DRAFT" | "ACTIVE";
};

export type CapabilityKnowledgeDocument = {
  id: string;
  workspace_id: string;
  profile_id: string;
  entity_type: "PROFILE" | "PRODUCT" | "SOLUTION" | "CASE" | "QUALIFICATION";
  entity_id: string | null;
  original_filename: string;
  mime_type: string;
  content_hash: string;
  size_bytes: number;
  version_no: number;
  sensitivity: "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
  status: "UPLOADED" | "PARSING" | "READY" | "FAILED" | "ARCHIVED";
  chunk_count: number;
  created_at: string;
  updated_at: string;
};

export type CapabilitySolution = {
  id: string;
  workspace_id: string;
  profile_id: string;
  name: string;
  industry: string | null;
  problem_statement: string;
  solution_summary: string;
  product_ids: string[];
  constraints: Array<Record<string, unknown>>;
  status: "DRAFT" | "ACTIVE" | "ARCHIVED";
  created_at: string;
  updated_at: string;
};

export type CapabilityCase = {
  id: string;
  workspace_id: string;
  profile_id: string;
  title: string;
  customer_industry: string | null;
  challenge: string;
  outcome: string;
  metrics: Array<Record<string, unknown>>;
  product_ids: string[];
  status: "DRAFT" | "ACTIVE" | "ARCHIVED";
  created_at: string;
  updated_at: string;
};

export type CapabilityQualification = {
  id: string;
  workspace_id: string;
  profile_id: string;
  qualification_type: "CERTIFICATION" | "QUALIFICATION" | "LICENSE" | "SECURITY" | "OTHER";
  name: string;
  issuer: string | null;
  certificate_no: string | null;
  applicable_regions: string[];
  valid_from: string | null;
  valid_to: string | null;
  status: "DRAFT" | "ACTIVE" | "ARCHIVED";
  created_at: string;
  updated_at: string;
};

export type CreateCapabilitySolutionPayload = {
  name: string;
  industry?: string;
  problem_statement: string;
  solution_summary: string;
  product_ids: string[];
  constraints: Array<{ name: string }>;
  status: "DRAFT" | "ACTIVE";
};

export type CreateCapabilityCasePayload = {
  title: string;
  customer_industry?: string;
  challenge: string;
  outcome: string;
  metrics: Array<{ name: string }>;
  product_ids: string[];
  status: "DRAFT" | "ACTIVE";
};

export type CreateCapabilityQualificationPayload = {
  qualification_type: CapabilityQualification["qualification_type"];
  name: string;
  issuer?: string;
  certificate_no?: string;
  applicable_regions: string[];
  valid_from?: string;
  valid_to?: string;
  status: "DRAFT" | "ACTIVE";
};

export type MatchableClaim = {
  id: string;
  workspace_id: string;
  task_id: string;
  report_version_id: string | null;
  claim_text: string;
  claim_type: "FACT" | "INFERENCE" | "ASSUMPTION";
  opportunity_effect: string;
  status: "UNVERIFIED" | "SUPPORTED" | "CUSTOMER_CONFIRMED" | "CONFLICTED" | "EXPIRED" | "REFUTED";
  confidence: number;
  first_seen_at: string;
  last_verified_at: string | null;
  expires_at: string | null;
  evidence_links: Array<{ evidence_id: string; relation: "SUPPORTS" | "REFUTES"; weight: number }>;
};

export type ProductMatchReference = {
  domain: "CLAIM" | "INTERNAL";
  source_ref: string;
  label: string;
};

export type ProductMatchResult = {
  status: "MATCHED" | "PARTIAL" | "NO_MATCH" | "NEEDS_VALIDATION" | "BLOCKED";
  fit_verified: boolean;
  hard_blocker: boolean;
  eligible_claim_ids: string[];
  pending_claim_ids: string[];
  selected_product_ids: string[];
  evaluated_product_ids: string[];
  matched_product_ids: string[];
  matched_requirements: string[];
  capability_gaps: string[];
  limitations: string[];
  pending_verifications: string[];
  references: ProductMatchReference[];
  recommendation_score: number;
  evidence_confidence: number;
  information_completeness: number;
  missing_gate_layers: Array<"time" | "capability" | "gap" | "trigger" | "window" | "fit">;
  positive_factors: string[];
  negative_factors: string[];
  revalidation_conditions: string[];
  gate_refresh?: {
    status: "CREATED" | "SKIPPED_NO_BASE_GATE" | "SKIPPED_ANALYSIS_DATE_MISMATCH";
    source_gate_decision_id: string | null;
    gate_decision_id: string | null;
    gate_level: string | null;
    decision: string | null;
    reasons: string[];
  };
};

export type ProductMatchPayload = {
  task_id: string;
  claim_ids: string[];
  product_ids: string[];
  analysis_as_of_date: string;
  target_industry?: string;
  target_region?: string;
  mandatory_qualifications?: string[];
};

export type ProductMatchSnapshot = {
  id: string;
  workspace_id: string;
  task_id: string;
  profile_id: string;
  created_by: string | null;
  analysis_as_of_date: string;
  input_hash: string;
  input_json: Record<string, unknown>;
  status: ProductMatchResult["status"];
  result_json: ProductMatchResult;
  created_at: string;
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || "能力中心请求失败");
  }
  return response.json();
}

export async function listCapabilityProfiles(includeArchived = false): Promise<CapabilityProfile[]> {
  const data = await request<{ items: CapabilityProfile[] }>(
    `/api/capability-profiles?include_archived=${includeArchived}`,
  );
  return data.items;
}

export function getCapabilityProfile(profileId: string): Promise<CapabilityProfile> {
  return request(`/api/capability-profiles/${profileId}`);
}

export function createCapabilityProfile(payload: CreateCapabilityProfilePayload): Promise<CapabilityProfile> {
  return request("/api/capability-profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function setDefaultCapabilityProfile(profileId: string): Promise<CapabilityProfile> {
  return request(`/api/capability-profiles/${profileId}/default`, { method: "POST" });
}

export function archiveCapabilityProfile(
  profileId: string,
  replacementDefaultId: string | null = null,
): Promise<CapabilityProfile> {
  return request(`/api/capability-profiles/${profileId}/archive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ replacement_default_id: replacementDefaultId }),
  });
}

export async function listCapabilityProducts(profileId: string): Promise<CapabilityProduct[]> {
  const data = await request<{ items: CapabilityProduct[] }>(
    `/api/capability-profiles/${profileId}/products`,
  );
  return data.items;
}

export function createCapabilityProduct(
  profileId: string,
  payload: CreateCapabilityProductPayload,
): Promise<CapabilityProduct> {
  return request(`/api/capability-profiles/${profileId}/products`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function archiveCapabilityProduct(productId: string): Promise<CapabilityProduct> {
  return request(`/api/capability-products/${productId}/archive`, { method: "POST" });
}

export async function listCapabilityDocuments(profileId: string): Promise<CapabilityKnowledgeDocument[]> {
  const data = await request<{ items: CapabilityKnowledgeDocument[] }>(
    `/api/capability-profiles/${profileId}/documents`,
  );
  return data.items;
}

export function uploadCapabilityDocument(input: {
  profileId: string;
  file: File;
  entityType: "PROFILE" | "PRODUCT";
  entityId?: string;
  sensitivity: "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
}): Promise<CapabilityKnowledgeDocument> {
  const body = new FormData();
  body.append("file", input.file);
  body.append("entity_type", input.entityType);
  if (input.entityId) body.append("entity_id", input.entityId);
  body.append("sensitivity", input.sensitivity);
  return request(`/api/capability-profiles/${input.profileId}/documents`, { method: "POST", body });
}

export function archiveCapabilityDocument(documentId: string): Promise<CapabilityKnowledgeDocument> {
  return request(`/api/capability-knowledge-documents/${documentId}/archive`, { method: "POST" });
}

export async function listCapabilitySolutions(profileId: string): Promise<CapabilitySolution[]> {
  const data = await request<{ items: CapabilitySolution[] }>(`/api/capability-profiles/${profileId}/solutions`);
  return data.items;
}

export function createCapabilitySolution(
  profileId: string,
  payload: CreateCapabilitySolutionPayload,
): Promise<CapabilitySolution> {
  return request(`/api/capability-profiles/${profileId}/solutions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listCapabilityCases(profileId: string): Promise<CapabilityCase[]> {
  const data = await request<{ items: CapabilityCase[] }>(`/api/capability-profiles/${profileId}/cases`);
  return data.items;
}

export function createCapabilityCase(
  profileId: string,
  payload: CreateCapabilityCasePayload,
): Promise<CapabilityCase> {
  return request(`/api/capability-profiles/${profileId}/cases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listCapabilityQualifications(profileId: string): Promise<CapabilityQualification[]> {
  const data = await request<{ items: CapabilityQualification[] }>(
    `/api/capability-profiles/${profileId}/qualifications`,
  );
  return data.items;
}

export function createCapabilityQualification(
  profileId: string,
  payload: CreateCapabilityQualificationPayload,
): Promise<CapabilityQualification> {
  return request(`/api/capability-profiles/${profileId}/qualifications`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function archiveCapabilityPortfolioItem(
  itemType: "solutions" | "cases" | "qualifications",
  itemId: string,
): Promise<{ id: string; status: "ARCHIVED" }> {
  return request(`/api/capability-portfolio/${itemType}/${itemId}/archive`, { method: "POST" });
}

export async function listTaskClaims(taskId: string): Promise<MatchableClaim[]> {
  const data = await request<{ items: MatchableClaim[] }>(`/api/claims?task_id=${encodeURIComponent(taskId)}`);
  return data.items;
}

export function previewProductMatch(
  profileId: string,
  payload: ProductMatchPayload,
): Promise<ProductMatchResult> {
  return request(`/api/capability-profiles/${profileId}/product-matches/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function saveProductMatch(
  profileId: string,
  payload: ProductMatchPayload,
): Promise<ProductMatchSnapshot> {
  return request(`/api/capability-profiles/${profileId}/product-matches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listProductMatchSnapshots(taskId: string): Promise<ProductMatchSnapshot[]> {
  const data = await request<{ items: ProductMatchSnapshot[] }>(
    `/api/tasks/${taskId}/product-match-snapshots`,
  );
  return data.items;
}
