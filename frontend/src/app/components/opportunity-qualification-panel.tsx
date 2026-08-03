"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import {
  assessHypothesisQualification,
  type QualificationCriterionStatus,
  type QualificationFramework,
} from "@/lib/opportunities";
import type { WorkbenchClaim, WorkbenchHypothesis } from "@/lib/target-accounts";


type Props = {
  hypothesis: WorkbenchHypothesis;
  claims: WorkbenchClaim[];
  frameworks: QualificationFramework[];
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
};

type CriterionDraft = {
  status: QualificationCriterionStatus;
  claimIds: string[];
  note: string;
};

const STATUS_OPTIONS: Array<{ value: QualificationCriterionStatus; label: string }> = [
  { value: "UNKNOWN", label: "尚未确认" },
  { value: "SUPPORTED", label: "公开/内部证据支持" },
  { value: "CUSTOMER_CONFIRMED", label: "客户已确认" },
  { value: "NEGATIVE", label: "反向或否定" },
];

const percent = (value: number) => `${Math.round(value * 100)}%`;

export function OpportunityQualificationPanel({
  hypothesis,
  claims,
  frameworks,
  onChanged,
  onError,
}: Props) {
  const [frameworkId, setFrameworkId] = useState(frameworks[0]?.id ?? "");
  const [drafts, setDrafts] = useState<Record<string, CriterionDraft>>({});
  const [summary, setSummary] = useState("");
  const [editing, setEditing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const framework = frameworks.find((item) => item.id === frameworkId) ?? frameworks[0] ?? null;
  const eligibleClaimIds = useMemo(
    () => new Set([...hypothesis.supporting_claim_ids, ...hypothesis.refuting_claim_ids]),
    [hypothesis.refuting_claim_ids, hypothesis.supporting_claim_ids],
  );
  const eligibleClaims = useMemo(
    () => claims.filter((claim) => eligibleClaimIds.has(claim.id)),
    [claims, eligibleClaimIds],
  );

  useEffect(() => {
    if (!framework) {
      setDrafts({});
      return;
    }
    setFrameworkId(framework.id);
    setDrafts(Object.fromEntries(framework.criteria.map((criterion) => [
      criterion.key,
      { status: "UNKNOWN", claimIds: [], note: "" },
    ])));
  }, [framework?.id]);

  const updateDraft = (key: string, patch: Partial<CriterionDraft>) => {
    setDrafts((current) => ({
      ...current,
      [key]: { ...(current[key] ?? { status: "UNKNOWN", claimIds: [], note: "" }), ...patch },
    }));
  };

  const submit = async () => {
    if (!framework) {
      onError("请先发布一套商机资格框架");
      return;
    }
    for (const criterion of framework.criteria) {
      const draft = drafts[criterion.key];
      if (
        (draft?.status === "SUPPORTED" || draft?.status === "CUSTOMER_CONFIRMED")
        && draft.claimIds.length === 0
      ) {
        onError(`${criterion.label} 标记为有证据时必须选择 Claim`);
        return;
      }
    }
    setSubmitting(true);
    try {
      await assessHypothesisQualification(hypothesis.id, {
        framework_id: framework.id,
        criteria: framework.criteria.map((criterion) => ({
          criterion_key: criterion.key,
          status: drafts[criterion.key]?.status ?? "UNKNOWN",
          claim_ids: drafts[criterion.key]?.claimIds ?? [],
          note: drafts[criterion.key]?.note ?? "",
        })),
        summary: summary.trim(),
      });
      setEditing(false);
      setSummary("");
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "资格评估失败");
    } finally {
      setSubmitting(false);
    }
  };

  const latest = hypothesis.latest_qualification;
  return (
    <section className="mt-4 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-neutral-600">商机资格卡</p>
          {latest ? (
            <p className="mt-1 text-sm text-neutral-700">
              {latest.framework_key} V{latest.framework_version} · 得分 {percent(latest.score)} · 完整度 {percent(latest.information_completeness)}
            </p>
          ) : (
            <p className="mt-1 text-sm text-neutral-500">尚未执行结构化资格评估。</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {latest && <StatusBadge status={latest.gate_result} label={latest.gate_result} />}
          {frameworks.length > 0 && hypothesis.status !== "CONVERTED" && (
            <Button type="button" size="sm" variant="secondary" onClick={() => setEditing((value) => !value)}>
              {latest ? "重新评估" : "开始评估"}
            </Button>
          )}
        </div>
      </div>
      {latest && (
        <div className="mt-3 text-sm text-neutral-700">
          <p>{latest.summary}</p>
          {latest.hard_blockers.length > 0 && <p className="mt-1 text-red-700">存在 {latest.hard_blockers.length} 项硬阻断。</p>}
          {latest.missing_fields.length > 0 && <p className="mt-1 text-amber-700">待确认：{latest.missing_fields.join("、")}</p>}
        </div>
      )}
      {frameworks.length === 0 && (
        <p className="mt-3 text-sm text-amber-700">Workspace 尚未发布资格框架，暂不能生成资格卡。</p>
      )}

      {editing && framework && (
        <div className="mt-4 space-y-4 border-t border-neutral-200 pt-4">
          <label className="block text-sm font-medium text-neutral-700">
            资格标准
            <select
              value={framework.id}
              onChange={(event) => setFrameworkId(event.target.value)}
              className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2"
            >
              {frameworks.map((item) => (
                <option key={item.id} value={item.id}>{item.name} · {item.methodology} · V{item.version_no}</option>
              ))}
            </select>
          </label>
          <p className="text-xs text-neutral-500">
            系统按权重、硬阻断和完整度确定性计算；“客户已确认”必须引用状态为 CUSTOMER_CONFIRMED 的 Claim。
          </p>
          {framework.criteria.map((criterion) => {
            const draft = drafts[criterion.key] ?? { status: "UNKNOWN", claimIds: [], note: "" };
            return (
              <fieldset key={criterion.key} className="rounded-lg border border-neutral-200 bg-white p-3">
                <legend className="px-1 text-sm font-semibold text-neutral-900">
                  {criterion.label}{criterion.required ? " *" : ""} · 权重 {criterion.weight}
                </legend>
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="text-xs font-medium text-neutral-600">
                    判断状态
                    <select
                      value={draft.status}
                      onChange={(event) => updateDraft(criterion.key, {
                        status: event.target.value as QualificationCriterionStatus,
                        ...(event.target.value === "UNKNOWN" ? { claimIds: [] } : {}),
                      })}
                      className="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
                    >
                      {STATUS_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </label>
                  <label className="text-xs font-medium text-neutral-600">
                    引用 Claim（可多选）
                    <select
                      multiple
                      value={draft.claimIds}
                      disabled={draft.status === "UNKNOWN"}
                      onChange={(event) => updateDraft(
                        criterion.key,
                        { claimIds: Array.from(event.target.selectedOptions, (option) => option.value) },
                      )}
                      className="mt-1 min-h-20 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm disabled:bg-neutral-100"
                    >
                      {eligibleClaims.map((claim) => (
                        <option key={claim.id} value={claim.id}>{claim.status} · {claim.claim_text}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="mt-3 block text-xs font-medium text-neutral-600">
                  备注
                  <input
                    value={draft.note}
                    onChange={(event) => updateDraft(criterion.key, { note: event.target.value })}
                    maxLength={2000}
                    className="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
                  />
                </label>
              </fieldset>
            );
          })}
          <label className="block text-sm font-medium text-neutral-700">
            本次评估摘要（可选）
            <textarea
              value={summary}
              onChange={(event) => setSummary(event.target.value)}
              rows={2}
              maxLength={4000}
              className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2"
            />
          </label>
          <div className="flex gap-2">
            <Button type="button" size="sm" isLoading={submitting} onClick={() => void submit()}>生成资格卡</Button>
            <Button type="button" size="sm" variant="ghost" disabled={submitting} onClick={() => setEditing(false)}>取消</Button>
          </div>
        </div>
      )}
    </section>
  );
}
