"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { StatusBadge } from "@/components/ui/workspace";
import {
  listCapabilityProducts,
  listCapabilityProfiles,
  listTaskClaims,
  previewProductMatch,
  saveProductMatch,
  type CapabilityProduct,
  type CapabilityProfile,
  type MatchableClaim,
  type ProductMatchPayload,
  type ProductMatchResult,
} from "@/lib/capabilities";

type Props = {
  taskId: string;
  targetIndustry?: string | null;
  targetRegion?: string | null;
  initialProfileId?: string | null;
};

const STATUS_COPY: Record<ProductMatchResult["status"], string> = {
  MATCHED: "已匹配",
  PARTIAL: "部分匹配",
  NO_MATCH: "暂无匹配",
  NEEDS_VALIDATION: "需要验证",
  BLOCKED: "硬性阻断",
};
const STATUS_STYLE: Record<ProductMatchResult["status"], string> = {
  MATCHED: "border-green-200 bg-green-50 text-green-700",
  PARTIAL: "border-amber-200 bg-amber-50 text-amber-700",
  NO_MATCH: "border-neutral-200 bg-neutral-100 text-neutral-700",
  NEEDS_VALIDATION: "border-amber-200 bg-amber-50 text-amber-700",
  BLOCKED: "border-red-200 bg-red-50 text-red-700",
};
const GATE_LAYER_LABEL: Record<ProductMatchResult["missing_gate_layers"][number], string> = {
  time: "时间证据",
  capability: "客户能力基线",
  gap: "未满足缺口",
  trigger: "当前触发",
  window: "介入窗口",
  fit: "产品适配",
};

const today = () => new Date().toISOString().slice(0, 10);

function toggle(values: string[], id: string, checked: boolean) {
  return checked ? Array.from(new Set([...values, id])) : values.filter((item) => item !== id);
}

function itemLabel(item: Record<string, unknown>) {
  return String(item.name ?? item.label ?? item.description ?? JSON.stringify(item));
}

export function ProductMatchPanel({
  taskId,
  targetIndustry,
  targetRegion,
  initialProfileId,
}: Props) {
  const [profiles, setProfiles] = useState<CapabilityProfile[]>([]);
  const [profileId, setProfileId] = useState(initialProfileId ?? "");
  const [products, setProducts] = useState<CapabilityProduct[]>([]);
  const [claims, setClaims] = useState<MatchableClaim[]>([]);
  const [claimIds, setClaimIds] = useState<string[]>([]);
  const [productIds, setProductIds] = useState<string[]>([]);
  const [analysisDate, setAnalysisDate] = useState(today());
  const [result, setResult] = useState<ProductMatchResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<"preview" | "save" | null>(null);
  const { error: toastError, success: toastSuccess } = useToast();

  useEffect(() => {
    let active = true;
    Promise.all([listCapabilityProfiles(), listTaskClaims(taskId)])
      .then(([profileItems, claimItems]) => {
        if (!active) return;
        const available = profileItems.filter((item) => item.status === "ACTIVE");
        setProfiles(available);
        setClaims(claimItems);
        setClaimIds(claimItems.filter((item) => item.status === "CUSTOMER_CONFIRMED").map((item) => item.id));
        setProfileId((current) => current || available.find((item) => item.is_default)?.id || available[0]?.id || "");
      })
      .catch((error) => toastError(error instanceof Error ? error.message : "匹配数据加载失败"))
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [taskId]);

  useEffect(() => {
    if (!profileId) {
      setProducts([]);
      setProductIds([]);
      return;
    }
    let active = true;
    listCapabilityProducts(profileId)
      .then((items) => {
        if (!active) return;
        setProducts(items.filter((item) => item.status === "ACTIVE"));
        setProductIds([]);
        setResult(null);
      })
      .catch((error) => toastError(error instanceof Error ? error.message : "产品加载失败"));
    return () => { active = false; };
  }, [profileId]);

  const payload = useMemo<ProductMatchPayload>(() => ({
    task_id: taskId,
    claim_ids: claimIds,
    product_ids: productIds,
    analysis_as_of_date: analysisDate,
    target_industry: targetIndustry || undefined,
    target_region: targetRegion || undefined,
  }), [analysisDate, claimIds, productIds, targetIndustry, targetRegion, taskId]);

  useEffect(() => {
    setResult(null);
  }, [analysisDate, claimIds, productIds, profileId, targetIndustry, targetRegion]);

  const execute = async (mode: "preview" | "save") => {
    if (!profileId) {
      toastError("请先选择能力档案");
      return;
    }
    setRunning(mode);
    try {
      if (mode === "preview") {
        setResult(await previewProductMatch(profileId, payload));
      } else {
        const snapshot = await saveProductMatch(profileId, payload);
        setResult(snapshot.result_json);
        toastSuccess("已保存不可变匹配快照");
      }
    } catch (error) {
      toastError(error instanceof Error ? error.message : "产品匹配失败");
    } finally {
      setRunning(null);
    }
  };

  if (loading) return <Card className="p-6 text-sm text-neutral-500">正在加载需求与能力数据…</Card>;

  return (
    <section className="space-y-4" data-testid="product-match-panel">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-500">REQUIREMENT × CAPABILITY</p>
          <h2 className="mt-1 text-xl font-semibold text-neutral-950">需求—能力—缺口匹配</h2>
          <p className="mt-1 text-sm text-neutral-600">客户 Claim 与我方能力分别取证；产品不能反向创造客户需求。</p>
        </div>
        <label className="text-sm font-medium text-neutral-700">
          分析截止日
          <input
            type="date"
            value={analysisDate}
            onChange={(event) => setAnalysisDate(event.target.value)}
            className="ml-2 rounded-lg border border-neutral-950/20 bg-white px-3 py-2"
          />
        </label>
      </div>

      <label className="block text-sm font-medium text-neutral-700">
        企业能力档案
        <select
          value={profileId}
          onChange={(event) => setProfileId(event.target.value)}
          className="mt-2 w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-3 md:max-w-xl"
        >
          <option value="">请选择能力档案</option>
          {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
        </select>
      </label>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="p-5">
          <h3 className="font-semibold text-neutral-950">1. 客户需求 Claim</h3>
          <p className="mt-1 text-xs text-neutral-500">未确认 Claim 可以选择，但只会进入待验证项。</p>
          <div className="mt-4 space-y-3">
            {claims.length === 0 && <p className="text-sm text-neutral-500">当前任务暂无 Claim。</p>}
            {claims.map((claim) => (
              <label key={claim.id} className="block rounded-lg border border-neutral-950/10 p-3">
                <span className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={claimIds.includes(claim.id)}
                    onChange={(event) => setClaimIds(toggle(claimIds, claim.id, event.target.checked))}
                    className="mt-1"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm text-neutral-900">{claim.claim_text}</span>
                    <span className="mt-2 flex flex-wrap gap-2 text-xs text-neutral-500">
                      <span>{claim.status}</span><span>置信度 {Math.round(claim.confidence * 100)}%</span>
                      <span>{claim.claim_type}</span>
                    </span>
                  </span>
                </span>
              </label>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <h3 className="font-semibold text-neutral-950">2. 候选产品版本</h3>
          <p className="mt-1 text-xs text-neutral-500">允许不选择任何产品，系统将明确返回“暂无匹配”。</p>
          <div className="mt-4 space-y-3">
            {products.length === 0 && <p className="text-sm text-neutral-500">当前档案暂无已启用产品。</p>}
            {products.map((product) => (
              <label key={product.id} className="block rounded-lg border border-neutral-950/10 p-3">
                <span className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={productIds.includes(product.id)}
                    onChange={(event) => setProductIds(toggle(productIds, product.id, event.target.checked))}
                    className="mt-1"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-neutral-900">{product.name} v{product.version_label}</span>
                    <span className="mt-1 block text-xs text-neutral-500">{product.summary}</span>
                    {product.capabilities.length > 0 && (
                      <span className="mt-2 block text-xs text-neutral-600">
                        能力：{product.capabilities.slice(0, 3).map(itemLabel).join("、")}
                      </span>
                    )}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-semibold text-neutral-950">3. 匹配结果与缺口</h3>
            {result && (
              <StatusBadge
                status={result.status}
                label={STATUS_COPY[result.status]}
                className={STATUS_STYLE[result.status]}
              />
            )}
          </div>
          {!result ? (
            <p className="mt-4 text-sm text-neutral-500">选择 Claim 与产品后预览；预览不会保存业务快照。</p>
          ) : (
            <div className="mt-4 space-y-4 text-sm">
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded-lg bg-neutral-50 p-2"><b>{result.recommendation_score}</b><span className="block text-xs text-neutral-500">推荐分</span></div>
                <div className="rounded-lg bg-neutral-50 p-2"><b>{Math.round(result.evidence_confidence * 100)}%</b><span className="block text-xs text-neutral-500">证据置信度</span></div>
                <div className="rounded-lg bg-neutral-50 p-2"><b>{Math.round(result.information_completeness * 100)}%</b><span className="block text-xs text-neutral-500">信息完整度</span></div>
              </div>
              <p className="rounded-lg border border-neutral-200 bg-white p-3 text-xs text-neutral-600">推荐分只用于同一裁决等级内排序，不能抵消硬阻断、缺失证据或较低完整度。</p>
              {result.hard_blocker && <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs font-medium text-red-800">已命中产品适配硬阻断；无论推荐分多高，都不能提升为 G5。</p>}
              {result.gate_refresh && <p className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900">Gate 联动：{result.gate_refresh.status === "CREATED" ? `已创建 ${result.gate_refresh.gate_level} 新裁决` : result.gate_refresh.reasons.join("；")}</p>}
              <ResultList title="OIG 尚缺层" items={result.missing_gate_layers.map((item) => GATE_LAYER_LABEL[item])} empty="六层条件已完整" danger />
              <ResultList title="主要正向因素" items={result.positive_factors} empty="暂无可确认正向因素" />
              <ResultList title="主要负向因素" items={result.negative_factors} empty="暂无额外负向因素" danger />
              <ResultList title="已覆盖需求" items={result.matched_requirements} empty="暂无已覆盖需求" />
              <ResultList title="能力缺口" items={result.capability_gaps} empty="暂无能力缺口" danger />
              <ResultList title="限制与不适用场景" items={result.limitations} empty="暂无已知限制" danger />
              <ResultList title="待验证项" items={result.pending_verifications} empty="暂无待验证项" />
              <ResultList title="重新验证条件" items={result.revalidation_conditions} empty="暂无重验条件" />
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">引用</p>
                <ul className="mt-2 space-y-1 text-xs text-neutral-600">
                  {result.references.map((reference) => (
                    <li key={`${reference.domain}:${reference.source_ref}`}>[{reference.domain.toLowerCase()}] {reference.label}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </Card>
      </div>

      <div className="flex flex-wrap justify-end gap-3">
        <Button variant="secondary" disabled={running !== null || !profileId || !analysisDate} onClick={() => void execute("preview")}>
          {running === "preview" ? "正在计算…" : "预览匹配"}
        </Button>
        <Button variant="primary" disabled={running !== null || !profileId || !analysisDate || !result} onClick={() => void execute("save")}>
          {running === "save" ? "正在保存…" : "保存不可变快照"}
        </Button>
      </div>
    </section>
  );
}

function ResultList({ title, items, empty, danger = false }: {
  title: string;
  items: string[];
  empty: string;
  danger?: boolean;
}) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">{title}</p>
      {items.length === 0 ? <p className="mt-1 text-xs text-neutral-400">{empty}</p> : (
        <ul className={`mt-2 space-y-1 text-xs ${danger ? "text-red-700" : "text-neutral-700"}`}>
          {items.map((item) => <li key={item}>• {item}</li>)}
        </ul>
      )}
    </div>
  );
}
