"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import { decideHypothesis, type HypothesisDecision } from "@/lib/opportunities";
import type { WorkbenchHypothesis } from "@/lib/target-accounts";
import { NextBestActionCard } from "@/app/components/next-best-action-card";
import { OpportunityConversionPanel } from "@/app/components/opportunity-conversion-panel";
import { OpportunityQualificationPanel } from "@/app/components/opportunity-qualification-panel";
import type { QualificationFramework } from "@/lib/opportunities";
import type { WorkbenchClaim } from "@/lib/target-accounts";


type Props = {
  hypothesis: WorkbenchHypothesis;
  claims: WorkbenchClaim[];
  frameworks: QualificationFramework[];
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
};

const STATUS_LABEL: Record<string, string> = {
  PENDING_SALES_REVIEW: "待销售判断",
  SALES_ACCEPTED: "销售已接受",
  SALES_REJECTED: "销售已拒绝",
  DEFERRED: "已暂缓",
  CUSTOMER_VALIDATED: "客户已确认",
  VALIDATION_FAILED: "客户验证未通过",
  CONVERTED: "已转正式商机",
  EXPIRED: "已过期",
};

const ACTIONS: Record<string, Array<{ value: HypothesisDecision; label: string }>> = {
  PENDING_SALES_REVIEW: [
    { value: "ACCEPT", label: "接受并安排验证" },
    { value: "REJECT", label: "拒绝" },
    { value: "DEFER", label: "暂缓" },
  ],
  SALES_ACCEPTED: [
    { value: "CONFIRM_CUSTOMER", label: "客户已确认（需确认 Claim）" },
    { value: "FAIL_VALIDATION", label: "验证未通过" },
    { value: "DEFER", label: "暂缓" },
  ],
  SALES_REJECTED: [{ value: "REOPEN", label: "重新评估" }],
  DEFERRED: [
    { value: "REOPEN", label: "恢复评估" },
    { value: "REJECT", label: "拒绝" },
  ],
  VALIDATION_FAILED: [{ value: "REOPEN", label: "重新评估" }],
};

const percent = (value: number) => `${Math.round(value * 100)}%`;
const date = (value: string | null) => value ? new Date(value).toLocaleDateString("zh-CN") : "未设置";

export function OpportunityHypothesisCard({ hypothesis, claims, frameworks, onChanged, onError }: Props) {
  const actions = ACTIONS[hypothesis.status] ?? [];
  const [decision, setDecision] = useState<HypothesisDecision | null>(null);
  const [reason, setReason] = useState("");
  const [decisionDate, setDecisionDate] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!decision || !reason.trim()) {
      onError("请填写本次裁决原因");
      return;
    }
    if ((decision === "ACCEPT" || decision === "DEFER") && !decisionDate) {
      onError(decision === "ACCEPT" ? "接受时必须设置行动截止日期" : "暂缓时必须设置重新评估日期");
      return;
    }
    setSubmitting(true);
    try {
      await decideHypothesis(hypothesis.id, {
        decision,
        reason: reason.trim(),
        request_key: crypto.randomUUID(),
        ...(decision === "ACCEPT" ? { action_due_at: new Date(`${decisionDate}T23:59:59`).toISOString() } : {}),
        ...(decision === "DEFER" ? { deferred_until: new Date(`${decisionDate}T09:00:00`).toISOString() } : {}),
      });
      setDecision(null);
      setReason("");
      setDecisionDate("");
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "商机假设裁决失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <article className="rounded-lg border border-neutral-200 p-5" data-testid="opportunity-hypothesis">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h3 className="font-semibold text-neutral-950">{hypothesis.title}</h3><p className="mt-1 text-xs text-neutral-500">有效期至 {date(hypothesis.expires_at)}</p></div>
        <StatusBadge status={hypothesis.status} label={STATUS_LABEL[hypothesis.status] || hypothesis.status} />
      </div>
      <dl className="mt-4 grid gap-3 text-sm md:grid-cols-2">
        <div><dt className="text-neutral-500">客户问题假设</dt><dd className="mt-1 text-neutral-900">{hypothesis.customer_problem_hypothesis}</dd></div>
        <div><dt className="text-neutral-500">业务影响假设</dt><dd className="mt-1 text-neutral-900">{hypothesis.business_impact_hypothesis}</dd></div>
        <div><dt className="text-neutral-500">触发事件</dt><dd className="mt-1 text-neutral-900">{hypothesis.trigger_event}</dd></div>
        <div><dt className="text-neutral-500">判断质量</dt><dd className="mt-1 text-neutral-900">置信度 {percent(hypothesis.confidence)} · 信息完整度 {percent(hypothesis.information_completeness)}</dd></div>
      </dl>
      <OpportunityQualificationPanel
        hypothesis={hypothesis}
        claims={claims}
        frameworks={frameworks}
        onChanged={onChanged}
        onError={onError}
      />
      <OpportunityConversionPanel hypothesis={hypothesis} onChanged={onChanged} onError={onError} />
      {hypothesis.candidate_products.length > 0 && (
        <div className="mt-4 rounded-lg bg-lime-50/70 p-4">
          <p className="text-xs font-semibold text-neutral-600">候选产品</p>
          <div className="mt-2 space-y-2">
            {hypothesis.candidate_products.map((product) => (
              <p key={product.product_id} className="text-sm text-neutral-900">
                <strong>{product.name} {product.version_label}</strong> · 适配 {percent(product.fit_score)}{product.rationale ? ` · ${product.rationale}` : ""}
              </p>
            ))}
          </div>
        </div>
      )}
      <div className="mt-4 space-y-2">
        <p className="text-xs font-semibold text-neutral-600">下一步验证行动</p>
        {hypothesis.actions.length === 0 ? <p className="text-sm text-neutral-500">尚未生成行动卡。</p> : hypothesis.actions.map((action) => (
          <NextBestActionCard
            key={action.id}
            action={action}
            hypothesisStatus={hypothesis.status}
            onChanged={onChanged}
            onError={onError}
          />
        ))}
      </div>

      {actions.length > 0 && (
        <div className="mt-5 border-t border-neutral-200 pt-4">
          <p className="text-xs font-semibold text-neutral-600">人工裁决</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {actions.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => { setDecision(item.value); setDecisionDate(""); }}
                className={`rounded-full border px-3 py-1.5 text-sm font-medium ${decision === item.value ? "border-neutral-950 bg-neutral-950 text-white" : "border-neutral-300 bg-white text-neutral-800"}`}
              >
                {item.label}
              </button>
            ))}
          </div>
          {decision && (
            <div className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
              <label className="block text-sm font-medium text-neutral-700">
                裁决原因
                <textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} maxLength={1000} className="mt-2 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
              </label>
              {(decision === "ACCEPT" || decision === "DEFER") && (
                <label className="mt-3 block text-sm font-medium text-neutral-700">
                  {decision === "ACCEPT" ? "行动截止日期" : "重新评估日期"}
                  <input type="date" value={decisionDate} onChange={(event) => setDecisionDate(event.target.value)} className="ml-3 rounded-lg border border-neutral-300 bg-white px-3 py-2" />
                </label>
              )}
              <div className="mt-3 flex gap-2">
                <Button type="button" size="sm" isLoading={submitting} onClick={() => void submit()}>确认裁决</Button>
                <Button type="button" size="sm" variant="ghost" disabled={submitting} onClick={() => setDecision(null)}>取消</Button>
              </div>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
