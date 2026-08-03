"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/providers/auth-provider";
import { useConfig } from "@/components/providers/config-provider";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { BatchProgress } from "@/app/components/batch-progress";
import { BatchFieldMapper } from "@/app/components/batch-field-mapper";
import { BatchDryRunResult } from "@/app/components/batch-dry-run-result";
import { BatchCostEstimate } from "@/app/components/batch-cost-estimate";
import { BatchTemplateCenter } from "@/app/components/batch-template-center";
import {
  previewFile,
  validateRows,
  dryRunImport,
  createBatchImport,
  type FieldMappingItem,
  type BatchCandidateRow,
  type ValidateRowResult,
  type DryRunResponse,
  type BatchTemplateDefinition,
} from "@/lib/batch-import";
import { listCapabilityProfiles, type CapabilityProfile } from "@/lib/capabilities";

// ── 步骤 ──────────────────────────────────────────────────────────────

type WizardStep = "upload" | "mapping" | "validate" | "dryrun" | "confirm";

const STEP_LABELS = [
  { label: "上传", key: "upload" },
  { label: "映射", key: "mapping" },
  { label: "校验", key: "validate" },
  { label: "采样", key: "dryrun" },
  { label: "执行", key: "confirm" },
];

function stepIndex(step: WizardStep): number {
  return STEP_LABELS.findIndex((s) => s.key === step);
}

export default function BatchNewPage() {
  const router = useRouter();
  const { user, isLoading: authLoading, authState } = useAuth();
  const { status: configStatus, isLoading: configLoading, error: configError } = useConfig();
  const { error: toastError, success: toastSuccess } = useToast();
  const executionBlocked = configLoading || Boolean(configError) || !configStatus?.execution_ready;
  const executionBlockMessage = configError
    ? "无法确认系统执行状态，请稍后重试。"
    : "当前配置仅支持浏览，完成 Provider 验证后才能执行批量任务。";

  useEffect(() => {
    if (authState === "unauthenticated") router.push("/login?redirect=/batches/new");
  }, [authState, router]);

  // ── 向导状态 ──────────────────────────────────────────────────────
  const [step, setStep] = useState<WizardStep>("upload");
  const [loading, setLoading] = useState(false);

  // 上传
  const [filename, setFilename] = useState("");
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [importTemplateId, setImportTemplateId] = useState<BatchTemplateDefinition["template_id"]>("standard_research");
  const [capabilityProfiles, setCapabilityProfiles] = useState<CapabilityProfile[]>([]);
  const [capabilityProfileId, setCapabilityProfileId] = useState("");

  useEffect(() => {
    listCapabilityProfiles()
      .then((items) => {
        setCapabilityProfiles(items);
        setCapabilityProfileId((current) => current || items.find((item) => item.is_default)?.id || "");
      })
      .catch((error) => toastError(error instanceof Error ? error.message : "能力档案加载失败"));
  }, []);

  // 字段映射
  const [fieldMapping, setFieldMapping] = useState<FieldMappingItem[]>([]);

  // 数据行
  const [candidateRows, setCandidateRows] = useState<BatchCandidateRow[]>([]);
  const [manualText, setManualText] = useState("");

  // 验证
  const [validateResults, setValidateResults] = useState<ValidateRowResult[]>([]);
  const [validateSummary, setValidateSummary] = useState({ valid: 0, warning: 0, error: 0, total: 0 });

  // Dry Run
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);

  // 批次设置
  const [batchName, setBatchName] = useState("");

  // ── Step 1: 上传 ──────────────────────────────────────────────────

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const ext = file.name.toLowerCase();
    if (!ext.endsWith(".csv") && !ext.endsWith(".xlsx")) {
      toastError("仅支持 .csv / .xlsx 文件");
      e.target.value = "";
      return;
    }

    setFilename(file.name);
    setUploadedFile(file);

    setLoading(true);
    try {
      const preview = await previewFile(file);
      setImportTemplateId(preview.template_id as BatchTemplateDefinition["template_id"]);
      setFieldMapping(preview.field_mapping);
      setCandidateRows(preview.candidate_rows);

      setStep("mapping");
      toastSuccess(`已识别 ${preview.source_row_count} 条数据`);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "文件解析失败");
    } finally {
      setLoading(false);
      e.target.value = "";
    }
  };

  const handleManualParse = async () => {
    const lines = manualText.split(/\r?\n/);
    const rows: BatchCandidateRow[] = [];
    lines.forEach((line, index) => {
      if (!line.trim()) return;
      const parts = line.split(/[,;，；\t]/);
      const row: BatchCandidateRow = {
        source_row_index: index + 1,
        company_name: (parts[0] || "").trim() || null,
        demand_direction: (parts[1] || "").trim()
          || (importTemplateId === "opportunity_discovery" ? "自动发现潜在需求与商机线索" : null),
        industry: (parts[2] || "").trim() || null,
        region: (parts[3] || "").trim() || null,
      };
      rows.push(row);
    });
    if (rows.length === 0) {
      toastError(importTemplateId === "opportunity_discovery"
        ? "未识别到有效企业名称，每行填写一家企业。"
        : "未识别到有效行。格式：企业名称,需求方向[,行业,地区]");
      return;
    }
    setCandidateRows(rows);
    const mapping: FieldMappingItem[] = [
      { standard_field: "company_name", detected_header: "粘贴输入", confidence: "high" },
      { standard_field: "demand_direction", detected_header: "粘贴输入", confidence: "high" },
    ];
    if (rows.some((row) => row.industry)) mapping.push({ standard_field: "industry", detected_header: "粘贴输入第3列", confidence: "high" });
    if (rows.some((row) => row.region)) mapping.push({ standard_field: "region", detected_header: "粘贴输入第4列", confidence: "high" });
    setFieldMapping(mapping);
    await runValidation(rows);
    toastSuccess(`已识别并校验 ${rows.length} 条数据`);
  };

  // ── Step 2→3: 验证 ──────────────────────────────────────────────

  const runValidation = async (rows: BatchCandidateRow[]) => {
    if (rows.length === 0) {
      toastError("没有可验证的数据");
      return;
    }
    setLoading(true);
    try {
      const result = await validateRows(rows, importTemplateId);
      setValidateResults(result.rows);
      setValidateSummary({
        valid: result.valid_count,
        warning: result.warning_count,
        error: result.error_count,
        total: result.total_rows,
      });
      setStep("validate");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "验证失败");
    } finally {
      setLoading(false);
    }
  };

  const handleValidate = async () => runValidation(candidateRows);

  const executableRows = () => validateResults.flatMap((result) =>
    result.normalized_row ? [result.normalized_row] : []
  );
  const executableRowCount = validateResults.filter((result) => result.normalized_row !== null).length;

  // ── Step 3→4: Dry Run ────────────────────────────────────────────

  const handleDryRun = async () => {
    if (executionBlocked) {
      toastError(executionBlockMessage);
      return;
    }
    if (importTemplateId === "opportunity_discovery" && !capabilityProfileId) {
      toastError("自动商机发现必须选择企业能力档案");
      return;
    }
    setLoading(true);
    try {
      const validRows = executableRows();
      if (validRows.length === 0) {
        toastError("没有有效的数据行可用于 Dry Run");
        setLoading(false);
        return;
      }
      const result = await dryRunImport(
        validRows.slice(0, 10),
        importTemplateId,
        Math.min(3, validRows.length),
        capabilityProfileId || null,
      );
      setDryRunResult(result);
      setStep("dryrun");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Dry Run 失败");
    } finally {
      setLoading(false);
    }
  };

  // ── Step 4→5: 创建 ───────────────────────────────────────────────

  const handleCreate = async () => {
    if (executionBlocked) {
      toastError(executionBlockMessage);
      return;
    }
    if (!batchName.trim()) {
      toastError("请填写批次名称");
      return;
    }
    if (importTemplateId === "opportunity_discovery" && !capabilityProfileId) {
      toastError("自动商机发现必须选择企业能力档案");
      return;
    }
    const validRows = executableRows();
    if (validRows.length === 0) {
      toastError("没有有效数据行可创建任务");
      return;
    }

    setLoading(true);
    try {
      const result = await createBatchImport(
        batchName.trim(), validRows, importTemplateId, capabilityProfileId || null,
      );
      toastSuccess(`批次创建成功，共 ${result.total_tasks} 个任务`);
      router.push(`/batches/${result.batch_id}`);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "创建批次失败");
      setLoading(false);
    }
  };

  // ── 跳过步骤 ──────────────────────────────────────────────────────

  const skipDryRun = () => {
    if (executionBlocked) {
      toastError(executionBlockMessage);
      return;
    }
    setStep("confirm");
  };

  // ── 渲染 ─────────────────────────────────────────────────────────

  if (authLoading || authState === "unavailable" || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-neutral-500">加载中...</p>
      </main>
    );
  }

  return (
    <PageShell>
      <PageHeader
        eyebrow="BATCH WIZARD"
        title="批量导入任务"
        description="上传 CSV/Excel 或粘贴数据，向导式创建批量分析任务"
      />

      {/* 进度条 */}
      <BatchProgress
        steps={STEP_LABELS.map((s) => ({
          label: s.label,
          status:
            stepIndex(step) > stepIndex(s.key as WizardStep)
              ? "done"
              : stepIndex(step) === stepIndex(s.key as WizardStep)
              ? "current"
              : "pending",
        }))}
      />

      <Card variant="bordered" padding="lg">
        {/* ════ Step 1: 上传 ════ */}
        {step === "upload" && (
          <div className="space-y-6">
            <h3 className="text-lg font-semibold text-neutral-900">上传数据文件</h3>

            <BatchTemplateCenter
              selectedId={importTemplateId}
              onSelect={setImportTemplateId}
              onError={toastError}
            />

            {importTemplateId === "opportunity_discovery" && (
              <div className="rounded-lg border border-neutral-950/10 bg-lime-50/70 p-4">
                <label className="block text-sm font-medium text-neutral-800">
                  用哪套企业能力挖掘商机？ <span className="text-red-500">*</span>
                  <select
                    value={capabilityProfileId}
                    onChange={(event) => setCapabilityProfileId(event.target.value)}
                    className="mt-2 w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5"
                  >
                    <option value="">请选择能力档案</option>
                    {capabilityProfiles.map((profile) => (
                      <option key={profile.id} value={profile.id}>{profile.name}{profile.is_default ? "（默认）" : ""}</option>
                    ))}
                  </select>
                </label>
                {capabilityProfiles.length === 0 && (
                  <p className="mt-2 text-sm text-amber-700">尚无能力档案，请先到“能力中心”创建产品与资料。</p>
                )}
              </div>
            )}

            {/* 文件上传区 */}
            <div
              onClick={() => fileInputRef.current?.click()}
              className={`cursor-pointer rounded-lg border border-dashed p-8 text-center transition-colors ${
                filename
                  ? "border-neutral-950 bg-lime-100/70"
                  : "border-neutral-950/20 bg-white/70 hover:border-neutral-950/50"
              }`}
            >
              <svg className="w-10 h-10 mx-auto mb-3 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              {loading ? (
                <p className="text-sm text-neutral-500">正在解析...</p>
              ) : filename ? (
                <>
                  <p className="text-sm font-medium text-neutral-950">{filename}</p>
                  <p className="mt-1 text-xs text-neutral-500">点击重新选择</p>
                </>
              ) : (
                <>
                  <p className="text-sm font-medium text-neutral-700">点击选择文件</p>
                  <p className="text-xs text-neutral-500 mt-1">
                    支持 .csv / .xlsx；上传后自动识别模板与版本
                  </p>
                </>
              )}
              <input ref={fileInputRef} type="file" accept=".csv,.xlsx" className="hidden" onChange={handleFileChange} disabled={loading} />
            </div>

            {/* 分割线 */}
            <div className="flex items-center gap-3">
              <div className="flex-1 h-px bg-neutral-200" />
              <span className="text-xs text-neutral-400">或</span>
              <div className="flex-1 h-px bg-neutral-200" />
            </div>

            {/* 粘贴输入 */}
            <div>
              <label className="mb-2 block text-sm font-medium text-neutral-700">粘贴数据</label>
              <textarea
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                placeholder={importTemplateId === "opportunity_discovery"
                  ? "每行一个目标企业\n例如：\n中国移动\n华为技术\n阿里巴巴"
                  : "每行一个客户，逗号分隔\n例如：\n中国移动,客服中心建设,电信\n华为技术,云计算平台采购,科技,深圳"}
                rows={6}
                className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-3 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-neutral-950/10 resize-none"
              />
              <div className="mt-3 flex items-center gap-3">
                <button
                  type="button"
                  onClick={handleManualParse}
                  className="inline-flex items-center rounded-full border border-neutral-950/10 bg-white px-4 py-2 text-sm font-medium text-neutral-950 hover:bg-neutral-100"
                >
                  解析文本
                </button>
                <span className="text-xs text-neutral-400">
                  {importTemplateId === "opportunity_discovery" ? "格式：每行一个企业名称" : "格式：企业名称,需求方向[,行业,地区]"}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* ════ Step 2: 字段映射 ════ */}
        {step === "mapping" && (
          <BatchFieldMapper
            mapping={fieldMapping}
            previewRows={candidateRows.slice(0, 5)}
            onConfirm={handleValidate}
            onBack={() => { setStep("upload"); setFilename(""); setUploadedFile(null); }}
          />
        )}

        {/* ════ Step 3: 验证 ════ */}
        {step === "validate" && (
          <div className="space-y-5">
            <h3 className="text-lg font-semibold text-neutral-900">数据校验</h3>

            {/* 概要 */}
            <div className="grid grid-cols-4 gap-2">
              {[
                { label: "总计", value: validateSummary.total, color: "text-neutral-900" },
                { label: "✓ 有效", value: validateSummary.valid, color: "text-green-600" },
                { label: "⚠ 警告", value: validateSummary.warning, color: "text-yellow-600" },
                { label: "✗ 错误", value: validateSummary.error, color: "text-red-600" },
              ].map((item) => (
                <div key={item.label} className="rounded-lg border border-neutral-950/10 p-3 text-center">
                  <div className={`text-lg font-semibold ${item.color}`}>{item.value}</div>
                  <div className="text-xs text-neutral-500">{item.label}</div>
                </div>
              ))}
            </div>

            {/* 详细列表 */}
            <div className="max-h-80 overflow-y-auto rounded-lg border border-neutral-950/10">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-neutral-50">
                  <tr>
                    <th className="text-left px-3 py-2 text-neutral-500 font-medium">#</th>
                    <th className="text-left px-3 py-2 text-neutral-500 font-medium">企业</th>
                    <th className="text-left px-3 py-2 text-neutral-500 font-medium">需求</th>
                    <th className="text-center px-3 py-2 text-neutral-500 font-medium">状态</th>
                    <th className="text-left px-3 py-2 text-neutral-500 font-medium">说明</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-100">
                  {validateResults.slice(0, 50).map((r) => {
                    const candidate = candidateRows.find((row) => row.source_row_index === r.source_row_index);
                    return (
                    <tr key={r.source_row_index} className={
                      r.validation_status === "error" ? "bg-red-50/30" : r.validation_status === "warning" ? "bg-yellow-50/30" : ""
                    }>
                      <td className="px-3 py-1.5 text-neutral-400">{r.source_row_index}</td>
                      <td className="px-3 py-1.5 text-neutral-900 truncate max-w-[120px]">{candidate?.company_name || "—"}</td>
                      <td className="px-3 py-1.5 text-neutral-600 truncate max-w-[160px]">{candidate?.demand_direction || "—"}</td>
                      <td className="px-3 py-1.5 text-center">
                        <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                          r.validation_status === "valid" ? "bg-green-100 text-green-700" :
                          r.validation_status === "warning" ? "bg-yellow-100 text-yellow-700" :
                          "bg-red-100 text-red-700"
                        }`}>
                          {r.validation_status === "valid" ? "有效" : r.validation_status === "warning" ? "警告" : "错误"}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-xs text-neutral-500">{r.error_message || "—"}</td>
                    </tr>
                  );})}
                </tbody>
              </table>
            </div>

            {validateResults.length > 50 && (
              <p className="text-xs text-neutral-400">仅显示前 50 条，共 {validateResults.length} 条</p>
            )}

            {executionBlocked && !configLoading && (
              <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {executionBlockMessage}
              </p>
            )}

            {/* 按钮 */}
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={handleDryRun}
                disabled={executableRowCount === 0 || loading || executionBlocked}
                className="inline-flex items-center rounded-full bg-neutral-950 px-5 py-2.5 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
              >
                {loading ? "执行中..." : "开始 Dry Run 采样"}
              </button>
              <button type="button" onClick={skipDryRun} disabled={executionBlocked} className="text-sm text-neutral-500 hover:text-neutral-950 disabled:cursor-not-allowed disabled:opacity-50">
                跳过采样，直接创建
              </button>
              <button type="button" onClick={() => setStep("mapping")} className="text-sm text-neutral-500 hover:text-neutral-950 ml-auto">
                ← 返回映射
              </button>
            </div>
          </div>
        )}

        {/* ════ Step 4: Dry Run 结果 ════ */}
        {step === "dryrun" && dryRunResult && (
          <div className="space-y-6">
            <h3 className="text-lg font-semibold text-neutral-900">采样执行结果</h3>

            <BatchDryRunResult samples={dryRunResult.samples} />
            <BatchCostEstimate estimate={dryRunResult.cost_estimate} />

            {/* 按钮 */}
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={() => setStep("confirm")}
                disabled={executionBlocked}
                className="inline-flex items-center rounded-full bg-neutral-950 px-5 py-2.5 text-sm font-medium text-white hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                确认，继续创建
              </button>
              <button type="button" onClick={() => setStep("validate")} className="text-sm text-neutral-500 hover:text-neutral-950">
                ← 返回校验
              </button>
            </div>
          </div>
        )}

        {/* ════ Step 5: 确认创建 ════ */}
        {step === "confirm" && (
          <div className="space-y-6">
            <h3 className="text-lg font-semibold text-neutral-900">确认创建批量任务</h3>

            {/* 汇总信息 */}
            <div className="rounded-lg border border-neutral-950/10 p-4">
              <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                <div>
                  <span className="text-neutral-500">有效任务：</span>
                  <span className="font-medium text-neutral-900">{executableRowCount}</span>
                </div>
                <div>
                  <span className="text-neutral-500">警告任务：</span>
                  <span className="font-medium text-yellow-700">{validateSummary.warning}</span>
                </div>
                <div>
                  <span className="text-neutral-500">错误任务：</span>
                  <span className="font-medium text-red-600">{validateSummary.error}</span>
                </div>
                <div>
                  <span className="text-neutral-500">Dry Run：</span>
                  <span className="font-medium text-neutral-900">
                    {dryRunResult ? "已完成" : "已跳过"}
                  </span>
                </div>
                <div>
                  <span className="text-neutral-500">研究模式：</span>
                  <span className="font-medium text-neutral-900">{importTemplateId === "opportunity_discovery" ? "自动商机发现" : "定向研究"}</span>
                </div>
                {importTemplateId === "opportunity_discovery" && (
                  <div>
                    <span className="text-neutral-500">能力档案：</span>
                    <span className="font-medium text-neutral-900">{capabilityProfiles.find((item) => item.id === capabilityProfileId)?.name || "未选择"}</span>
                  </div>
                )}
              </div>
              {validateSummary.error > 0 && (
                <p className="mt-3 text-sm text-yellow-600">
                  注意：{validateSummary.error} 条错误行将被自动剔除
                </p>
              )}
            </div>

            {/* 批次名称 */}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-neutral-700">
                批次名称 <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={batchName}
                onChange={(e) => setBatchName(e.target.value)}
                placeholder="例如：2026年Q3供应商分析"
                className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 text-base focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
              />
            </div>

            {/* 按钮 */}
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={handleCreate}
                disabled={executionBlocked || loading || !batchName.trim() || (importTemplateId === "opportunity_discovery" && !capabilityProfileId)}
                className="inline-flex items-center rounded-full bg-neutral-950 px-5 py-2.5 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
              >
                {loading ? "创建中..." : `创建批量任务（${executableRowCount} 个）`}
              </button>
              <button
                type="button"
                onClick={() => setStep(dryRunResult ? "dryrun" : "validate")}
                className="text-sm text-neutral-500 hover:text-neutral-950"
              >
                ← 返回上一步
              </button>
            </div>
          </div>
        )}
      </Card>
    </PageShell>
  );
}
