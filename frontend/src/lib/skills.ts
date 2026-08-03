/** Skill V2 标准文件、版本、Dry Run 与发布 API。 */
import { authenticatedFetch } from "@/lib/auth";

export type SkillVersion = {
  id: string;
  version: number;
  status: "DRAFT" | "COMPILED" | "EVALUATED" | "PUBLISHED" | "REJECTED" | "ARCHIVED";
  content_hash: string;
  compiled_spec: {
    name: string;
    description: string;
    license?: string | null;
    version: number;
    triggers?: string[];
    questions?: string[];
    sources?: string[];
    budget?: Record<string, number>;
    stop_conditions?: string[];
    report_sections?: string[];
    dependencies?: string[];
    execution_phase?: "research" | "evaluation";
    output_fields?: string[];
    quality_thresholds?: Record<string, number>;
    allowed_tools?: string[];
    data_domains?: string[];
    dependency_conditions?: Record<string, Record<string, unknown>>;
  };
  compiled_at: string | null;
  published_at: string | null;
  created_at: string;
};

export type SkillCompiledSpec = SkillVersion["compiled_spec"];

export type SkillCompilePreview = {
  valid: boolean;
  compiled_spec: SkillCompiledSpec | null;
  errors: string[];
  warnings: string[];
};

export type SkillSummary = {
  id: string;
  name: string;
  display_name: string;
  description: string;
  scope: "SYSTEM" | "WORKSPACE";
  status: "DRAFT" | "PUBLISHED" | "ARCHIVED";
  editable: boolean;
  current_version_id: string | null;
  latest_version: SkillVersion | null;
  created_at: string;
  updated_at: string;
};

export type SkillDetail = SkillSummary & { versions: SkillVersion[] };

export type SkillGraphNode = {
  skill_id: string;
  version_id: string;
  name: string;
  display_name: string;
  version: number;
  status: string;
  execution_phase: "research" | "evaluation";
  allowed_tools: string[];
  data_domains: Array<"external" | "customer_private" | "internal">;
  editable: boolean;
};

export type SkillGraphEdge = {
  parent_version_id: string;
  child_skill_id: string;
  min_version: number;
  condition: {
    all?: Array<{
      field: string;
      operator: "EQ" | "NEQ" | "IN" | "NOT_IN" | "EXISTS";
      value: string | number | boolean | Array<string | number | boolean>;
    }>;
  };
};

export type SkillGraph = {
  root_skill_id: string;
  root_version_id: string;
  nodes: SkillGraphNode[];
  edges: SkillGraphEdge[];
  execution_order: string[];
};

export type SkillGraphPreview = {
  markdown: string;
  diff_text: string;
  compiled_version: number;
  graph: SkillGraph;
};

export type SkillMutation = {
  skill: SkillSummary;
  version: SkillVersion | null;
};

export type SkillDryRun = {
  tool_plan: string[];
  budget: Record<string, number>;
  external_execution: boolean;
};

export type SkillEvalObservation = {
  actual_trigger?: boolean;
  answered_questions: string[];
  used_sources: string[];
  report_sections: string[];
  evidence_count?: number;
  critical_claim_count?: number;
  cited_critical_claim_count?: number;
  cost?: number;
  manual_score?: number;
};

export type SkillEvalCaseInput = {
  name: string;
  input: { query: string; observation: SkillEvalObservation };
  expected_trigger: boolean;
  expected_outputs: {
    required_questions: string[];
    required_sources: string[];
    required_report_sections: string[];
    min_evidence_count?: number;
    min_citation_coverage?: number;
    max_cost?: number;
    min_manual_score?: number;
  };
};

export type SkillEvalCase = SkillEvalCaseInput & {
  id: string;
  skill_id: string;
  enabled: boolean;
  created_at: string;
};

export type SkillEvalRun = {
  id: string;
  version_id: string;
  case_id: string;
  status: "PASSED" | "FAILED" | "ERROR";
  metrics: Record<string, number | null>;
  result: {
    evaluator: string;
    checks: Record<string, boolean>;
    failures: string[];
    external_execution: boolean;
  };
  model: string | null;
  initiated_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type SkillEvalSuite = {
  passed: boolean;
  version_status: SkillVersion["status"];
  runs: SkillEvalRun[];
};

export type RuntimeSkillBrief = {
  name: string;
  description: string;
  version: number;
  execution_order: string[];
  research_skills: string[];
  evaluation_skills: string[];
};

export type SkillConversionIssue = {
  code: string;
  severity: "INFO" | "WARNING" | "BLOCKING";
  message: string;
  path: string;
};

export type SkillImportJob = {
  id: string;
  source_type: "GITHUB" | "OFFLINE_ARCHIVE";
  repo_url: string | null;
  commit_sha: string | null;
  path: string;
  request_hash: string;
  snapshot_hash: string | null;
  conversion_result: {
    source_format: "PROJECT_STANDARD" | "CODEX_CLAUDE" | "GENERIC_MARKDOWN";
    missing_required: string[];
    inferred_fields: string[];
    removed_fields: string[];
    issues: SkillConversionIssue[];
    license_status: "DECLARED" | "FILE_PRESENT" | "UNKNOWN";
    license_value: string | null;
    publishable: boolean;
  };
  merge_result: {
    status?: "CLEAN" | "CONFLICT" | "NO_CHANGES";
    base_version_id?: string;
    local_version_id?: string;
    local_version?: number;
    base_commit_sha?: string;
    upstream_commit_sha?: string;
    conflicts?: Array<{ base_start: number; base_end: number }>;
  };
  diff_text: string;
  mock_result: Record<string, unknown>;
  status: "QUEUED" | "FETCHING" | "PREVIEWED" | "BLOCKED" | "FAILED" | "MOCKED" | "IMPORTED" | "EXPIRED";
  dispatch_attempt: number;
  error_code: string | null;
  error_message: string | null;
  expires_at: string;
  started_at: string | null;
  finished_at: string | null;
  confirmed_at: string | null;
  imported_at: string | null;
  skill_id: string | null;
  version_id: string | null;
  upstream_source_id: string | null;
  created_at: string;
  updated_at: string;
};

export type SkillImportMock = {
  job: SkillImportJob;
  compiled_name: string;
  execution_phase: string;
  synthetic_questions: string[];
  planned_sources: string[];
  expected_output_fields: string[];
  network_calls: number;
  model_calls: number;
  filesystem_writes: number;
};

export type SkillImportConfirmation = {
  job: SkillImportJob;
  skill: SkillSummary;
  version: SkillVersion;
  created_skill: boolean;
};

async function apiError(response: Response, fallback: string): Promise<Error> {
  const detail = await response.json().then((body) => body.detail).catch(() => null);
  return new Error(detail || `${fallback} (${response.status})`);
}

export async function fetchSkills(includeArchived = false): Promise<SkillSummary[] | null> {
  try {
    const response = await authenticatedFetch(
      `/api/skills?include_archived=${includeArchived}`,
    );
    if (!response.ok) return null;
    return ((await response.json()) as { skills: SkillSummary[] }).skills;
  } catch {
    return null;
  }
}

export async function fetchRuntimeSkills(): Promise<RuntimeSkillBrief[] | null> {
  try {
    const response = await authenticatedFetch("/api/skills/runtime");
    if (!response.ok) return null;
    return ((await response.json()) as { skills: RuntimeSkillBrief[] }).skills;
  } catch {
    return null;
  }
}

export async function fetchSkillDetail(id: string): Promise<SkillDetail | null> {
  try {
    const response = await authenticatedFetch(`/api/skills/${id}`);
    return response.ok ? response.json() : null;
  } catch {
    return null;
  }
}

export async function fetchSkillGraph(
  skillId: string,
  versionId: string,
): Promise<SkillGraph> {
  const response = await authenticatedFetch(
    `/api/skills/${skillId}/versions/${versionId}/graph`,
  );
  if (!response.ok) throw await apiError(response, "加载 Skill 编排失败");
  return response.json();
}

export async function previewSkillGraph(
  skillId: string,
  versionId: string,
  edges: Array<Pick<SkillGraphEdge, "child_skill_id" | "min_version" | "condition">>,
): Promise<SkillGraphPreview> {
  const response = await authenticatedFetch(
    `/api/skills/${skillId}/versions/${versionId}/graph/preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edges }),
    },
  );
  if (!response.ok) throw await apiError(response, "预览 Skill 编排失败");
  return response.json();
}

export async function fetchSkillSource(
  skillId: string,
  versionId: string,
): Promise<string | null> {
  try {
    const response = await authenticatedFetch(
      `/api/skills/${skillId}/versions/${versionId}/source`,
    );
    if (!response.ok) return null;
    return ((await response.json()) as { markdown: string }).markdown;
  } catch {
    return null;
  }
}

export async function compileSkillPreview(source: string): Promise<SkillCompilePreview> {
  const response = await authenticatedFetch("/api/skills/compile-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  });
  if (!response.ok) throw await apiError(response, "Skill 编译预览失败");
  return response.json();
}

export async function createSkill(
  markdown: string,
  displayName?: string,
): Promise<SkillMutation> {
  const response = await authenticatedFetch("/api/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown, display_name: displayName || null }),
  });
  if (!response.ok) throw await apiError(response, "创建失败");
  return response.json();
}

export async function createSkillVersion(
  skillId: string,
  markdown: string,
): Promise<SkillMutation> {
  const response = await authenticatedFetch(`/api/skills/${skillId}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown }),
  });
  if (!response.ok) throw await apiError(response, "创建版本失败");
  return response.json();
}

export async function dryRunSkill(
  skillId: string,
  versionId: string,
): Promise<SkillDryRun> {
  const response = await authenticatedFetch(
    `/api/skills/${skillId}/versions/${versionId}/dry-run`,
    { method: "POST" },
  );
  if (!response.ok) throw await apiError(response, "预演失败");
  return response.json();
}

export async function publishSkill(
  skillId: string,
  versionId: string,
): Promise<SkillMutation> {
  const response = await authenticatedFetch(
    `/api/skills/${skillId}/versions/${versionId}/publish`,
    { method: "POST" },
  );
  if (!response.ok) throw await apiError(response, "发布失败");
  return response.json();
}

export async function fetchSkillEvalCases(skillId: string): Promise<SkillEvalCase[]> {
  const response = await authenticatedFetch(`/api/skills/${skillId}/eval-cases`);
  if (!response.ok) throw await apiError(response, "加载黄金用例失败");
  return response.json();
}

export async function createSkillEvalCase(
  skillId: string,
  value: SkillEvalCaseInput,
): Promise<SkillEvalCase> {
  const response = await authenticatedFetch(`/api/skills/${skillId}/eval-cases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
  if (!response.ok) throw await apiError(response, "创建黄金用例失败");
  return response.json();
}

export async function disableSkillEvalCase(
  skillId: string,
  caseId: string,
): Promise<SkillEvalCase> {
  const response = await authenticatedFetch(
    `/api/skills/${skillId}/eval-cases/${caseId}/disable`,
    { method: "POST" },
  );
  if (!response.ok) throw await apiError(response, "停用黄金用例失败");
  return response.json();
}

export async function evaluateSkillVersion(
  skillId: string,
  versionId: string,
): Promise<SkillEvalSuite> {
  const response = await authenticatedFetch(
    `/api/skills/${skillId}/versions/${versionId}/evaluate`,
    { method: "POST" },
  );
  if (!response.ok) throw await apiError(response, "评测失败");
  return response.json();
}

export async function fetchSkillEvalRuns(
  skillId: string,
  versionId: string,
): Promise<SkillEvalRun[]> {
  const response = await authenticatedFetch(
    `/api/skills/${skillId}/versions/${versionId}/eval-runs`,
  );
  if (!response.ok) throw await apiError(response, "加载评测记录失败");
  return response.json();
}

export async function archiveSkill(skillId: string): Promise<SkillMutation> {
  const response = await authenticatedFetch(`/api/skills/${skillId}/archive`, {
    method: "POST",
  });
  if (!response.ok) throw await apiError(response, "归档失败");
  return response.json();
}

export async function previewGitHubSkillImport(input: {
  repoUrl: string;
  commitSha: string;
  path: string;
}): Promise<SkillImportJob> {
  const response = await authenticatedFetch("/api/skills/imports/github/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repo_url: input.repoUrl,
      commit_sha: input.commitSha,
      path: input.path,
    }),
  });
  if (!response.ok) throw await apiError(response, "GitHub Skill 获取失败");
  return response.json();
}

export async function previewOfflineSkillImport(
  file: File,
  path: string,
): Promise<SkillImportJob> {
  const form = new FormData();
  form.append("file", file);
  form.append("path", path);
  const response = await authenticatedFetch("/api/skills/imports/offline/preview", {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw await apiError(response, "离线 Skill 读取失败");
  return response.json();
}

export async function previewSkillUpstreamUpdate(
  skillId: string,
  commitSha: string,
): Promise<SkillImportJob> {
  const response = await authenticatedFetch(`/api/skills/${skillId}/upstream/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ commit_sha: commitSha }),
  });
  if (!response.ok) throw await apiError(response, "检查 Skill 上游更新失败");
  return response.json();
}

export async function mockSkillImport(jobId: string): Promise<SkillImportMock> {
  const response = await authenticatedFetch(`/api/skills/imports/${jobId}/mock`, {
    method: "POST",
  });
  if (!response.ok) throw await apiError(response, "Skill Mock 失败");
  return response.json();
}

export async function fetchSkillImportJob(jobId: string): Promise<SkillImportJob> {
  const response = await authenticatedFetch(`/api/skills/imports/${jobId}`);
  if (!response.ok) throw await apiError(response, "读取 Skill 导入状态失败");
  return response.json();
}

export async function confirmSkillImport(
  jobId: string,
  conflictAction: "CREATE_NEW" | "CREATE_VERSION",
): Promise<SkillImportConfirmation> {
  const response = await authenticatedFetch(`/api/skills/imports/${jobId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed: true, conflict_action: conflictAction }),
  });
  if (!response.ok) throw await apiError(response, "Skill 导入失败");
  return response.json();
}
