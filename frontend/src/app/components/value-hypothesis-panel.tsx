"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import { calculateValueHypothesis, listValueHypotheses, type ValueHypothesis } from "@/lib/opportunities";
import type { WorkbenchClaim } from "@/lib/target-accounts";


type Props = { opportunityId: string; claims: WorkbenchClaim[]; onError: (message: string) => void };
type Source = "CUSTOMER_PROVIDED" | "INDUSTRY_BENCHMARK" | "USER_ASSUMPTION";

export function ValueHypothesisPanel({ opportunityId, claims, onError }: Props) {
  const [versions, setVersions] = useState<ValueHypothesis[]>([]);
  const [editing, setEditing] = useState(false);
  const [benefit, setBenefit] = useState("");
  const [cost, setCost] = useState("");
  const [source, setSource] = useState<Source>("USER_ASSUMPTION");
  const [claimId, setClaimId] = useState("");
  const [status, setStatus] = useState<"NEEDS_VALIDATION" | "CUSTOMER_CONFIRMED">("NEEDS_VALIDATION");
  const [submitting, setSubmitting] = useState(false);
  const latest = versions[0] ?? null;

  const refresh = useCallback(async () => setVersions(await listValueHypotheses(opportunityId)), [opportunityId]);
  useEffect(() => { void refresh().catch((error) => onError(error instanceof Error ? error.message : "价值假设加载失败")); }, [onError, refresh]);

  const submit = async () => {
    if (source !== "USER_ASSUMPTION" && !claimId) {
      onError("客户参数或行业基准必须选择 Claim");
      return;
    }
    if (status === "CUSTOMER_CONFIRMED" && source !== "CUSTOMER_PROVIDED") {
      onError("客户确认态只允许使用客户提供的参数");
      return;
    }
    setSubmitting(true);
    try {
      await calculateValueHypothesis(opportunityId, {
        status,
        currency: "CNY",
        time_horizon_months: 12,
        inputs: [
          { key: "annual_benefit", label: "预计年度收益", value: benefit || null, unit: "CNY", source_type: source, ...(claimId ? { source_claim_id: claimId } : {}) },
          { key: "total_cost", label: "预计总投入", value: cost || null, unit: "CNY", source_type: source, ...(claimId ? { source_claim_id: claimId } : {}) },
        ],
        formulas: [
          { key: "net_benefit", label: "净收益", operation: "DIFFERENCE", operands: ["annual_benefit", "total_cost"], unit: "CNY" },
          { key: "roi", label: "投资回报率", operation: "RATIO", operands: ["net_benefit", "total_cost"], unit: "ratio" },
        ],
        sensitivity_scenarios: [],
      });
      setEditing(false);
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : "价值假设计算失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="mt-4 border-t border-neutral-200 pt-4">
      <div className="flex items-start justify-between gap-3">
        <div><p className="text-sm font-semibold text-neutral-900">价值假设</p><p className="mt-1 text-xs text-neutral-500">缺参数时保留为空，不生成伪精确 ROI，也不自动改写商机金额。</p></div>
        <Button type="button" size="sm" variant="secondary" onClick={() => setEditing((value) => !value)}>{latest ? "新建版本" : "开始测算"}</Button>
      </div>
      {latest && (
        <div className="mt-3 rounded-lg bg-neutral-50 p-3">
          <div className="flex items-center justify-between"><p className="text-sm font-medium">V{latest.version_no} · {latest.time_horizon_months ?? "-"} 个月</p><StatusBadge status={latest.status} label={latest.status} /></div>
          <div className="mt-2 grid gap-2 md:grid-cols-2">{latest.outputs.map((output) => <p key={output.key} className="text-sm text-neutral-700">{output.label}：{output.value === null ? "待补参数" : `${output.value} ${output.unit}`}</p>)}</div>
          {latest.missing_parameters.length > 0 && <p className="mt-2 text-xs text-amber-700">待补参数：{latest.missing_parameters.join("、")}</p>}
        </div>
      )}
      {editing && (
        <div className="mt-3 grid gap-3 rounded-lg bg-neutral-50 p-3 md:grid-cols-2">
          <label className="text-xs font-medium text-neutral-600">预计年度收益（CNY，可留空）<input inputMode="decimal" value={benefit} onChange={(event) => setBenefit(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm" /></label>
          <label className="text-xs font-medium text-neutral-600">预计总投入（CNY，可留空）<input inputMode="decimal" value={cost} onChange={(event) => setCost(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm" /></label>
          <label className="text-xs font-medium text-neutral-600">参数来源<select value={source} onChange={(event) => { setSource(event.target.value as Source); setClaimId(""); }} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm"><option value="USER_ASSUMPTION">用户假设</option><option value="INDUSTRY_BENCHMARK">行业基准</option><option value="CUSTOMER_PROVIDED">客户提供</option></select></label>
          <label className="text-xs font-medium text-neutral-600">来源 Claim<select value={claimId} disabled={source === "USER_ASSUMPTION"} onChange={(event) => setClaimId(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm disabled:bg-neutral-100"><option value="">请选择</option>{claims.filter((claim) => source !== "CUSTOMER_PROVIDED" || claim.status === "CUSTOMER_CONFIRMED").map((claim) => <option key={claim.id} value={claim.id}>{claim.status} · {claim.claim_text}</option>)}</select></label>
          <label className="text-xs font-medium text-neutral-600 md:col-span-2">结果状态<select value={status} onChange={(event) => setStatus(event.target.value as typeof status)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm"><option value="NEEDS_VALIDATION">价值假设，仍需客户验证</option><option value="CUSTOMER_CONFIRMED">客户已确认全部参数</option></select></label>
          <div className="flex gap-2 md:col-span-2"><Button type="button" size="sm" isLoading={submitting} onClick={() => void submit()}>计算并保存版本</Button><Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>取消</Button></div>
        </div>
      )}
    </section>
  );
}
