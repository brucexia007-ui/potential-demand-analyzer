"use client";

import { useEffect, useMemo, useState } from "react";
import {
  createSkillEvalCase,
  disableSkillEvalCase,
  evaluateSkillVersion,
  fetchSkillEvalCases,
  fetchSkillEvalRuns,
  type SkillEvalCase,
  type SkillEvalCaseInput,
  type SkillEvalRun,
  type SkillSummary,
} from "@/lib/skills";

type Props = {
  skill: SkillSummary;
  onClose: () => void;
  onEvaluated: () => Promise<void>;
};

type FormState = {
  name: string;
  query: string;
  expectedTrigger: boolean;
  actualTrigger: boolean;
  expectedQuestions: string;
  expectedSources: string;
  expectedReportSections: string;
  answeredQuestions: string;
  usedSources: string;
  reportSections: string;
  evidenceCount: string;
  criticalClaimCount: string;
  citedCriticalClaimCount: string;
  cost: string;
  manualScore: string;
  minEvidenceCount: string;
  minCitationCoverage: string;
  maxCost: string;
  minManualScore: string;
};

const CHECK_LABELS: Record<string, string> = {
  trigger: "触发判断",
  declared_questions: "Skill 问题声明",
  answered_questions: "问题回答覆盖",
  declared_sources: "Skill 信源声明",
  used_sources: "实际信源覆盖",
  declared_report_sections: "Skill 报告结构",
  observed_report_sections: "实际章节覆盖",
  evidence_count: "证据数量",
  citation_coverage: "关键结论引用率",
  cost: "成本",
  manual_score: "人工盲评分",
};

function lines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function optionalNumber(value: string): number | undefined {
  return value.trim() === "" ? undefined : Number(value);
}

function initialForm(skill: SkillSummary): FormState {
  const spec = skill.latest_version?.compiled_spec;
  return {
    name: `v${skill.latest_version?.version ?? 1} 发布门黄金用例`,
    query: "",
    expectedTrigger: true,
    actualTrigger: true,
    expectedQuestions: (spec?.questions || []).join("\n"),
    expectedSources: (spec?.sources || []).join("\n"),
    expectedReportSections: (spec?.report_sections || []).join("\n"),
    answeredQuestions: "",
    usedSources: "",
    reportSections: "",
    evidenceCount: "",
    criticalClaimCount: "",
    citedCriticalClaimCount: "",
    cost: "",
    manualScore: "",
    minEvidenceCount: "1",
    minCitationCoverage: "1",
    maxCost: "",
    minManualScore: "80",
  };
}

function buildCase(form: FormState): SkillEvalCaseInput {
  return {
    name: form.name.trim(),
    input: {
      query: form.query.trim(),
      observation: {
        actual_trigger: form.actualTrigger,
        answered_questions: lines(form.answeredQuestions),
        used_sources: lines(form.usedSources),
        report_sections: lines(form.reportSections),
        evidence_count: optionalNumber(form.evidenceCount),
        critical_claim_count: optionalNumber(form.criticalClaimCount),
        cited_critical_claim_count: optionalNumber(form.citedCriticalClaimCount),
        cost: optionalNumber(form.cost),
        manual_score: optionalNumber(form.manualScore),
      },
    },
    expected_trigger: form.expectedTrigger,
    expected_outputs: {
      required_questions: lines(form.expectedQuestions),
      required_sources: lines(form.expectedSources),
      required_report_sections: lines(form.expectedReportSections),
      min_evidence_count: optionalNumber(form.minEvidenceCount),
      min_citation_coverage: optionalNumber(form.minCitationCoverage),
      max_cost: optionalNumber(form.maxCost),
      min_manual_score: optionalNumber(form.minManualScore),
    },
  };
}

export function SkillEvalPanel({ skill, onClose, onEvaluated }: Props) {
  const version = skill.latest_version!;
  const [cases, setCases] = useState<SkillEvalCase[]>([]);
  const [runs, setRuns] = useState<SkillEvalRun[]>([]);
  const [form, setForm] = useState<FormState>(() => initialForm(skill));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const failedChecks = useMemo(
    () => Array.from(new Set(runs.flatMap((run) => run.result.failures || []))),
    [runs],
  );
  const enabledCaseCount = cases.filter((item) => item.enabled).length;

  const load = async () => {
    const [nextCases, nextRuns] = await Promise.all([
      fetchSkillEvalCases(skill.id),
      fetchSkillEvalRuns(skill.id, version.id),
    ]);
    setCases(nextCases);
    setRuns(nextRuns);
  };

  useEffect(() => {
    void load()
      .catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败"))
      .finally(() => setLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const addCase = async () => {
    if (!form.name.trim() || !form.query.trim()) {
      setError("请填写用例名称和真实样本问题");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createSkillEvalCase(skill.id, buildCase(form));
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    } finally {
      setBusy(false);
    }
  };

  const evaluate = async () => {
    setBusy(true);
    setError(null);
    try {
      const suite = await evaluateSkillVersion(skill.id, version.id);
      setRuns(suite.runs);
      await onEvaluated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "评测失败");
    } finally {
      setBusy(false);
    }
  };

  const disableCase = async (caseId: string) => {
    if (!window.confirm("确认停用这条黄金用例？历史评测记录会继续保留。")) return;
    setBusy(true);
    setError(null);
    try {
      await disableSkillEvalCase(skill.id, caseId);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "停用失败");
    } finally {
      setBusy(false);
    }
  };

  const fieldClass = "mt-1 w-full rounded-xl border border-neutral-200 px-3 py-2 text-sm outline-none focus:border-neutral-500";
  const textAreaClass = `${fieldClass} min-h-24 resize-y`;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold">黄金用例评测 · v{version.version}</h3>
          <p className="mt-1 text-xs leading-5 text-neutral-500">
            录入真实样本执行的观察结果，系统确定性检查触发、覆盖、证据、引用、成本和人工盲评分；本步骤不调用模型。
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-sm text-neutral-500">关闭</button>
      </div>

      {loading ? <p className="py-12 text-center text-sm text-neutral-400">加载评测数据…</p> : (
        <>
          <section className="rounded-2xl border border-neutral-200 p-4">
            <div className="flex items-center justify-between">
              <h4 className="font-medium">已启用黄金用例</h4>
              <span className="text-xs text-neutral-500">{enabledCaseCount} 条启用</span>
            </div>
            {cases.length > 0 ? (
              <div className="mt-3 space-y-2">
                {cases.map((item) => (
                  <div key={item.id} className="flex items-center justify-between gap-3 rounded-xl bg-neutral-50 px-3 py-2 text-sm">
                    <span className={item.enabled ? "" : "text-neutral-400 line-through"}>{item.name}</span>
                    {item.enabled ? (
                      <button type="button" disabled={busy} onClick={() => disableCase(item.id)} className="text-xs text-red-600 disabled:opacity-40">停用</button>
                    ) : <span className="text-xs text-neutral-400">已停用</span>}
                  </div>
                ))}
              </div>
            ) : <p className="mt-3 text-sm text-neutral-500">尚无用例。至少录入一个真实样本后才能评测和发布。</p>}
          </section>

          <details className="rounded-2xl border border-neutral-200 p-4" open={cases.length === 0}>
            <summary className="cursor-pointer font-medium">新增真实样本用例</summary>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <label className="text-xs text-neutral-600">用例名称<input className={fieldClass} value={form.name} onChange={(event) => update("name", event.target.value)} /></label>
              <label className="text-xs text-neutral-600">真实样本问题<input className={fieldClass} value={form.query} onChange={(event) => update("query", event.target.value)} placeholder="例如：研究某客户是否存在当前可验证的采购窗口" /></label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.expectedTrigger} onChange={(event) => update("expectedTrigger", event.target.checked)} />本用例期望触发</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.actualTrigger} onChange={(event) => update("actualTrigger", event.target.checked)} />样本执行时实际触发</label>
              <label className="text-xs text-neutral-600">期望覆盖的问题（每行一项）<textarea className={textAreaClass} value={form.expectedQuestions} onChange={(event) => update("expectedQuestions", event.target.value)} /></label>
              <label className="text-xs text-neutral-600">实际回答的问题（每行一项）<textarea className={textAreaClass} value={form.answeredQuestions} onChange={(event) => update("answeredQuestions", event.target.value)} /></label>
              <label className="text-xs text-neutral-600">期望使用的信源（每行一项）<textarea className={textAreaClass} value={form.expectedSources} onChange={(event) => update("expectedSources", event.target.value)} /></label>
              <label className="text-xs text-neutral-600">实际使用的信源（每行一项）<textarea className={textAreaClass} value={form.usedSources} onChange={(event) => update("usedSources", event.target.value)} /></label>
              <label className="text-xs text-neutral-600">期望报告章节（每行一项）<textarea className={textAreaClass} value={form.expectedReportSections} onChange={(event) => update("expectedReportSections", event.target.value)} /></label>
              <label className="text-xs text-neutral-600">实际报告章节（每行一项）<textarea className={textAreaClass} value={form.reportSections} onChange={(event) => update("reportSections", event.target.value)} /></label>
              <div className="grid grid-cols-2 gap-3">
                <NumberField label="实际证据数" value={form.evidenceCount} onChange={(value) => update("evidenceCount", value)} />
                <NumberField label="最低证据数" value={form.minEvidenceCount} onChange={(value) => update("minEvidenceCount", value)} />
                <NumberField label="关键结论数" value={form.criticalClaimCount} onChange={(value) => update("criticalClaimCount", value)} />
                <NumberField label="已引用结论数" value={form.citedCriticalClaimCount} onChange={(value) => update("citedCriticalClaimCount", value)} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <NumberField label="最低引用率（0-1）" value={form.minCitationCoverage} step="0.01" onChange={(value) => update("minCitationCoverage", value)} />
                <NumberField label="人工盲评分" value={form.manualScore} onChange={(value) => update("manualScore", value)} />
                <NumberField label="最低盲评分" value={form.minManualScore} onChange={(value) => update("minManualScore", value)} />
                <NumberField label="实际成本" value={form.cost} step="0.01" onChange={(value) => update("cost", value)} />
                <NumberField label="最高成本" value={form.maxCost} step="0.01" onChange={(value) => update("maxCost", value)} />
              </div>
            </div>
            <button type="button" onClick={addCase} disabled={busy} className="mt-4 rounded-full border border-neutral-900 px-4 py-2 text-sm disabled:opacity-50">保存黄金用例</button>
          </details>

          {runs.length > 0 && (
            <section className={`rounded-2xl border p-4 ${failedChecks.length ? "border-red-200 bg-red-50" : "border-emerald-200 bg-emerald-50"}`}>
              <h4 className="font-medium">最近评测：{failedChecks.length ? "未通过" : "已通过"}</h4>
              {failedChecks.length > 0
                ? <p className="mt-2 text-sm text-red-700">未通过项：{failedChecks.map((item) => CHECK_LABELS[item] || item).join("、")}</p>
                : <p className="mt-2 text-sm text-emerald-700">全部黄金用例通过，可以返回列表发布该版本。</p>}
            </section>
          )}

          {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-full border px-4 py-2 text-sm">返回</button>
            <button type="button" onClick={evaluate} disabled={busy || enabledCaseCount === 0} className="rounded-full bg-neutral-950 px-4 py-2 text-sm text-white disabled:opacity-40">{busy ? "评测中…" : `运行 ${enabledCaseCount} 条用例`}</button>
          </div>
        </>
      )}
    </div>
  );
}

function NumberField({ label, value, step = "1", onChange }: { label: string; value: string; step?: string; onChange: (value: string) => void }) {
  return <label className="text-xs text-neutral-600">{label}<input type="number" min="0" step={step} className="mt-1 w-full rounded-xl border border-neutral-200 px-3 py-2 text-sm" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}
