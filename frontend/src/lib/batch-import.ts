/** v3.1 WBS-19b: 批量导入 API 客户端 */

import { authenticatedFetch } from "@/lib/auth";

// ── 类型 ──────────────────────────────────────────────────────────────

export type FieldMappingItem = {
  standard_field: string;
  detected_header: string;
  confidence: "high" | "medium" | "manual";
};

export type PreviewResult = {
  filename: string;
  template_id: string;
  template_version: number;
  source_row_count: number;
  headers: string[];
  field_mapping: FieldMappingItem[];
  preview_candidates: BatchCandidateRow[];
  candidate_rows: BatchCandidateRow[];
  warnings: string[];
};

export type ImportRow = {
  company_name: string;
  demand_direction: string;
  industry?: string | null;
  region?: string | null;
  capability_profile_id?: string | null;
  disambiguation?: {
    official_website?: string;
    unified_social_credit_code?: string;
  } | null;
};

export type BatchCandidateRow = {
  source_row_index: number;
  company_name?: string | null;
  demand_direction?: string | null;
  industry?: string | null;
  region?: string | null;
  capability_profile_id?: string | null;
  disambiguation?: ImportRow["disambiguation"];
};

export type BatchTemplateDefinition = {
  template_id: "standard_research" | "opportunity_discovery";
  version: number;
  name: string;
  description: string;
  fields: Array<{
    key: string;
    label: string;
    required: boolean;
    description: string;
    example: string;
  }>;
};

export type ValidateRowResult = {
  source_row_index: number;
  validation_status: "valid" | "warning" | "error";
  sample_score: number;
  error_code: string | null;
  error_message: string | null;
  normalized_row: ImportRow | null;
};

export type ValidateResponse = {
  total_rows: number;
  valid_count: number;
  warning_count: number;
  error_count: number;
  rows: ValidateRowResult[];
};

export type DryRunSampleResult = {
  row_index: number;
  company_name: string;
  demand_direction: string;
  sample_score: number;
  rank: number;
  result: Record<string, unknown> | null;
};

export type CostEstimate = {
  estimated_total_tokens: number;
  estimated_total_time_minutes: number;
  monetary_cost: {
    status: "UNAVAILABLE";
    amount: null;
    currency: null;
    reason: string;
  };
  total_rows: number;
  sample_count: number;
  confidence: "low" | "medium" | "high";
  estimate_basis: string;
};

export type DryRunResponse = {
  samples: DryRunSampleResult[];
  cost_estimate: CostEstimate;
};

export type ImportCreateResponse = {
  batch_id: string;
  name: string;
  total_tasks: number;
  status: string;
  import_rows_count: number;
  accepted_rows: number;
  rejected_rows: number;
};

// ── 辅助 ──────────────────────────────────────────────────────────────

// ── API ────────────────────────────────────────────────────────────────

export async function listBatchTemplates(): Promise<BatchTemplateDefinition[]> {
  const response = await authenticatedFetch("/api/batches/import/templates");
  if (!response.ok) throw new Error(`模板目录加载失败 (${response.status})`);
  return (await response.json() as { items: BatchTemplateDefinition[] }).items;
}

export async function downloadBatchTemplate(
  templateId: BatchTemplateDefinition["template_id"],
  fileFormat: "xlsx" | "csv",
): Promise<void> {
  const response = await authenticatedFetch(
    `/api/batches/import/templates/${templateId}/download?file_format=${fileFormat}`,
  );
  if (!response.ok) throw new Error(`模板下载失败 (${response.status})`);
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = disposition.match(/filename="?([^";]+)"?/)?.[1]
    ?? `kanyikan_${templateId}.${fileFormat}`;
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** 上传文件预览（CSV/Excel → 字段映射 + 预览数据）*/
export async function previewFile(file: File): Promise<PreviewResult> {
  const formData = new FormData();
  formData.append("file", file);
  const resp = await authenticatedFetch("/api/batches/import/preview", {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({}));
    throw new Error(d.detail || `文件解析失败 (${resp.status})`);
  }
  return resp.json();
}

/** 验证导入行 */
export async function validateRows(
  candidateRows: BatchCandidateRow[],
  templateId: BatchTemplateDefinition["template_id"],
): Promise<ValidateResponse> {
  const resp = await authenticatedFetch("/api/batches/import/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidate_rows: candidateRows, template_id: templateId }),
  });
  if (!resp.ok) throw new Error("验证失败");
  return resp.json();
}

/** Dry Run 采样执行 */
export async function dryRunImport(
  rows: ImportRow[],
  templateId: BatchTemplateDefinition["template_id"],
  sampleCount = 2,
  capabilityProfileId?: string | null,
): Promise<DryRunResponse> {
  const resp = await authenticatedFetch("/api/batches/import/dry-run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rows,
      template_id: templateId,
      capability_profile_id: capabilityProfileId || undefined,
      sample_count: sampleCount,
      harness_config: { max_iterations: 1 },
    }),
  });
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({}));
    throw new Error(d.detail || `Dry Run 失败 (${resp.status})`);
  }
  return resp.json();
}

/** 创建批量导入任务 */
export async function createBatchImport(
  name: string,
  rows: ImportRow[],
  templateId: BatchTemplateDefinition["template_id"],
  capabilityProfileId?: string | null,
  skillId?: string | null
): Promise<ImportCreateResponse> {
  const resp = await authenticatedFetch("/api/batches/import/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      rows,
      template_id: templateId,
      capability_profile_id: capabilityProfileId || undefined,
      skill_id: skillId || undefined,
    }),
  });
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({}));
    throw new Error(d.detail || `创建失败 (${resp.status})`);
  }
  return resp.json();
}
