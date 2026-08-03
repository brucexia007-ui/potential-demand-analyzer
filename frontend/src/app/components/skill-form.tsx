"use client";

import { useMemo, useState } from "react";
import {
  compileSkillPreview,
  type SkillCompiledSpec,
  type SkillDetail,
} from "@/lib/skills";

type SkillFormValue = { markdown: string; displayName?: string };

type Props = {
  skill?: SkillDetail | null;
  source?: string;
  targetVersion: number;
  readOnly?: boolean;
  onSave: (value: SkillFormValue) => Promise<void>;
  onCancel: () => void;
  isSaving: boolean;
};

type GuidedValue = {
  name: string;
  description: string;
  license: string;
  executionPhase: "research" | "evaluation";
  allowedTools: string;
  dataDomains: string;
  dependencyConditions: string;
  triggers: string;
  questions: string;
  sources: string;
  budget: string;
  stopConditions: string;
  reportSections: string;
  dependencies: string;
  outputFields: string;
  qualityThresholds: string;
};

const DEFAULT_GUIDED: GuidedValue = {
  name: "",
  description: "",
  license: "",
  executionPhase: "research",
  allowedTools: "",
  dataDomains: "",
  dependencyConditions: "{}",
  triggers: "",
  questions: "需要研究的核心问题",
  sources: "目标企业官网\n政府或监管机构网站",
  budget: "max_external_calls: 20\nmax_input_tokens: 120000",
  stopConditions: "关键结论已有充分证据\n继续搜索的信息增益过低",
  reportSections: "执行摘要\n关键发现\n证据与反证\n下一步行动",
  dependencies: "",
  outputFields: "",
  qualityThresholds: "",
};

function items(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function keyValueLines(value?: Record<string, number>): string {
  return Object.entries(value || {}).map(([key, amount]) => `${key}: ${amount}`).join("\n");
}

function compiledToGuided(spec: SkillCompiledSpec): GuidedValue {
  return {
    name: spec.name,
    description: spec.description,
    license: spec.license || "",
    executionPhase: spec.execution_phase || "research",
    allowedTools: (spec.allowed_tools || []).join("\n"),
    dataDomains: (spec.data_domains || []).join("\n"),
    dependencyConditions: JSON.stringify(spec.dependency_conditions || {}, null, 2),
    triggers: (spec.triggers || []).join("\n"),
    questions: (spec.questions || []).join("\n"),
    sources: (spec.sources || []).join("\n"),
    budget: keyValueLines(spec.budget),
    stopConditions: (spec.stop_conditions || []).join("\n"),
    reportSections: (spec.report_sections || []).join("\n"),
    dependencies: (spec.dependencies || []).join("\n"),
    outputFields: (spec.output_fields || []).join("\n"),
    qualityThresholds: keyValueLines(spec.quality_thresholds),
  };
}

function listSection(title: string, value: string): string {
  const values = items(value);
  return values.length ? `## ${title}\n${values.map((item) => `- ${item}`).join("\n")}\n\n` : "";
}

function buildSource(value: GuidedValue, version: number): string {
  const dependencyConditions = JSON.parse(value.dependencyConditions || "{}");
  if (!dependencyConditions || Array.isArray(dependencyConditions) || typeof dependencyConditions !== "object") {
    throw new Error("依赖条件必须是 JSON 对象");
  }
  return [
    "---\n",
    `name: ${JSON.stringify(value.name.trim())}\n`,
    `description: ${JSON.stringify(value.description.trim())}\n`,
    value.license.trim() ? `license: ${JSON.stringify(value.license.trim())}\n` : "",
    "metadata:\n",
    `  version: ${JSON.stringify(String(version))}\n`,
    `  execution_phase: ${value.executionPhase}\n`,
    `  allowed_tools: ${JSON.stringify(items(value.allowedTools))}\n`,
    `  data_domains: ${JSON.stringify(items(value.dataDomains))}\n`,
    `  dependency_conditions: ${JSON.stringify(dependencyConditions)}\n`,
    "---\n",
    listSection("Triggers", value.triggers),
    listSection("Questions", value.questions),
    listSection("Sources", value.sources),
    listSection("Budget", value.budget),
    listSection("Stop Conditions", value.stopConditions),
    listSection("Report Structure", value.reportSections),
    listSection("Dependencies", value.dependencies),
    listSection("Output Fields", value.outputFields),
    listSection("Quality Thresholds", value.qualityThresholds),
  ].join("").trimEnd() + "\n";
}

function withVersion(source: string, version: number): string {
  return source.replace(/^(\s*version:\s*)["']?\d+["']?\s*$/m, `$1"${version}"`);
}

export function SkillForm({
  skill,
  source = "",
  targetVersion,
  readOnly = false,
  onSave,
  onCancel,
  isSaving,
}: Props) {
  const initialSource = useMemo(
    () => source ? withVersion(source, targetVersion) : buildSource(DEFAULT_GUIDED, targetVersion),
    [source, targetVersion],
  );
  const initialSpec = skill?.latest_version?.compiled_spec;
  const [mode, setMode] = useState<"guided" | "source">(readOnly ? "source" : "guided");
  const [displayName, setDisplayName] = useState(skill?.display_name || "");
  const [guided, setGuided] = useState<GuidedValue>(() => initialSpec ? compiledToGuided(initialSpec) : DEFAULT_GUIDED);
  const [guidedDirty, setGuidedDirty] = useState(false);
  const [rawSource, setRawSource] = useState(initialSource);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isCompiling, setIsCompiling] = useState(false);

  const setField = (field: keyof GuidedValue, value: string) => {
    setGuided((current) => ({ ...current, [field]: value }));
    setGuidedDirty(true);
    setNotice(null);
  };

  const switchMode = async (next: "guided" | "source") => {
    if (next === mode) return;
    setError(null);
    if (next === "source") {
      if (guidedDirty) {
        try {
          setRawSource(buildSource(guided, targetVersion));
          setNotice("源码已根据引导字段规范化；保存前请检查差异。");
          setGuidedDirty(false);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : "无法生成 SKILL.md");
          return;
        }
      }
      setMode("source");
      return;
    }

    setIsCompiling(true);
    try {
      const preview = await compileSkillPreview(rawSource);
      if (!preview.valid || !preview.compiled_spec) {
        setError(preview.errors.join("；") || "SKILL.md 编译失败");
        return;
      }
      setGuided(compiledToGuided(preview.compiled_spec));
      setGuidedDirty(false);
      setMode("guided");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "SKILL.md 编译预览失败");
    } finally {
      setIsCompiling(false);
    }
  };

  const submit = async () => {
    setError(null);
    let markdown = rawSource;
    try {
      if (mode === "guided" && guidedDirty) markdown = buildSource(guided, targetVersion);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法生成 SKILL.md");
      return;
    }
    const preview = await compileSkillPreview(markdown);
    if (!preview.valid) {
      setError(preview.errors.join("；") || "SKILL.md 编译失败");
      return;
    }
    await onSave({ markdown, displayName: displayName.trim() || undefined });
  };

  const textArea = (label: string, field: keyof GuidedValue, hint: string, rows = 3) => (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium text-neutral-700">{label}</span>
      <textarea
        value={guided[field]}
        onChange={(event) => setField(field, event.target.value)}
        disabled={readOnly}
        rows={rows}
        placeholder={hint}
        className="w-full resize-y rounded-xl border border-neutral-950/15 bg-white px-3 py-2 text-sm leading-6 outline-none focus:ring-2 focus:ring-neutral-950/10 disabled:bg-neutral-50"
      />
    </label>
  );

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-neutral-950">{readOnly ? "查看系统 Skill" : skill ? `创建 v${targetVersion}` : "新建 Skill"}</h3>
          <p className="mt-1 text-xs text-neutral-500">SKILL.md 是唯一规范来源；切换模式不会静默覆盖源码。</p>
        </div>
        <div className="flex rounded-full bg-neutral-100 p-1 text-xs">
          {(["guided", "source"] as const).map((value) => (
            <button key={value} type="button" disabled={isCompiling} onClick={() => void switchMode(value)} className={`rounded-full px-3 py-1.5 ${mode === value ? "bg-white text-neutral-950 shadow-sm" : "text-neutral-500"}`}>
              {value === "guided" ? "引导模式" : "SKILL.md"}
            </button>
          ))}
        </div>
      </div>

      {mode === "guided" ? (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="space-y-1.5"><span className="text-sm font-medium">显示名称</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} disabled={readOnly || Boolean(skill)} className="w-full rounded-xl border px-3 py-2 text-sm disabled:bg-neutral-50" /></label>
            <label className="space-y-1.5"><span className="text-sm font-medium">唯一标识</span><input value={guided.name} onChange={(event) => setField("name", event.target.value)} disabled={readOnly || Boolean(skill)} className="w-full rounded-xl border px-3 py-2 font-mono text-sm disabled:bg-neutral-50" /></label>
            <label className="space-y-1.5"><span className="text-sm font-medium">用途说明</span><input value={guided.description} onChange={(event) => setField("description", event.target.value)} disabled={readOnly} className="w-full rounded-xl border px-3 py-2 text-sm disabled:bg-neutral-50" /></label>
            <label className="space-y-1.5"><span className="text-sm font-medium">许可证</span><input value={guided.license} onChange={(event) => setField("license", event.target.value)} disabled={readOnly} className="w-full rounded-xl border px-3 py-2 text-sm disabled:bg-neutral-50" /></label>
            <label className="space-y-1.5"><span className="text-sm font-medium">执行阶段</span><select value={guided.executionPhase} onChange={(event) => setField("executionPhase", event.target.value)} disabled={readOnly} className="w-full rounded-xl border px-3 py-2 text-sm disabled:bg-neutral-50"><option value="research">研究</option><option value="evaluation">评估</option></select></label>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {textArea("触发条件", "triggers", "每行一项")}
            {textArea("研究问题 *", "questions", "每行一项", 5)}
            {textArea("信源策略 *", "sources", "每行一项", 5)}
            {textArea("允许工具", "allowedTools", "external_search")}
            {textArea("数据域", "dataDomains", "external")}
            {textArea("停止条件", "stopConditions", "每行一项")}
            {textArea("报告结构", "reportSections", "每行一项")}
            {textArea("二级 Skill", "dependencies", "child-skill@1")}
            {textArea("输出字段", "outputFields", "snake_case 字段")}
            {textArea("预算", "budget", "max_external_calls: 20")}
            {textArea("质量阈值", "qualityThresholds", "min_overall_score: 0.8")}
            {textArea("依赖条件 JSON", "dependencyConditions", "{}", 5)}
          </div>
        </div>
      ) : (
        <textarea value={rawSource} onChange={(event) => { setRawSource(event.target.value); setNotice(null); }} disabled={readOnly} rows={28} spellCheck={false} className="w-full resize-y rounded-xl border border-neutral-950/15 bg-neutral-950 p-4 font-mono text-xs leading-6 text-neutral-100 outline-none disabled:opacity-80" />
      )}

      {notice && <p className="rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">{notice}</p>}
      {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      <div className="flex justify-end gap-3 border-t border-neutral-950/10 pt-4">
        <button type="button" onClick={onCancel} className="rounded-full border px-4 py-2 text-sm">{readOnly ? "关闭" : "取消"}</button>
        {!readOnly && <button type="button" onClick={() => void submit()} disabled={isSaving || isCompiling} className="rounded-full bg-neutral-950 px-5 py-2 text-sm font-medium text-white disabled:opacity-50">{isSaving ? "编译保存中…" : skill ? `保存为 v${targetVersion}` : "创建并编译"}</button>}
      </div>
    </div>
  );
}
