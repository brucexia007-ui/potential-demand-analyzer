"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/auth";
import {
  listCapabilityProducts,
  listCapabilityProfiles,
  type CapabilityProduct,
  type CapabilityProfile,
} from "@/lib/capabilities";
import { fetchRuntimeSkills, type RuntimeSkillBrief } from "@/lib/skills";

type FunnelStage = {
  key: string;
  label: string;
  count: number;
  conversion_from_previous: number | null;
};

type DashboardMetrics = {
  generated_at: string;
  cohort_basis: "RESEARCH_TASK_CREATED_AT";
  funnel: FunnelStage[];
  outcomes: {
    signal_accepted: number;
    signal_rejected: number;
    customer_validated: number;
    customer_invalidated: number;
    no_opportunity: number;
    identification_error: number;
    signal_acceptance_rate: number | null;
    customer_validation_rate: number | null;
  };
  amounts: {
    by_currency: Array<{
      currency: string;
      confirmed_pipeline_amount: string;
      confirmed_won_amount: string;
    }>;
    missing_or_unconfirmed_count: number;
  };
  execution: {
    external_call_count: number;
    settled_call_count: number;
    input_tokens: number;
    output_tokens: number;
    average_call_latency_ms: number | null;
    average_research_duration_seconds: number | null;
    settled_costs: Array<{ currency: string; settled_amount: string }>;
    saved_labor_hours: number | null;
    saved_labor_hours_status: "NOT_CONFIGURED" | "AVAILABLE";
  };
  dwell_times: Array<{
    key: string;
    label: string;
    sample_count: number;
    average_seconds: number | null;
  }>;
};

type Period = "30D" | "90D" | "YEAR" | "ALL";

const percent = (value: number | null) => value === null ? "—" : `${(value * 100).toFixed(1)}%`;

const duration = (seconds: number | null) => {
  if (seconds === null) return "暂无样本";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} 分钟`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`;
  return `${(seconds / 86400).toFixed(1)} 天`;
};

const number = (value: string | number) => Number(value).toLocaleString("zh-CN", {
  maximumFractionDigits: 2,
});

function periodStart(period: Period): string | null {
  if (period === "ALL") return null;
  const value = new Date();
  if (period === "30D") value.setDate(value.getDate() - 30);
  if (period === "90D") value.setDate(value.getDate() - 90);
  if (period === "YEAR") value.setMonth(0, 1), value.setHours(0, 0, 0, 0);
  return value.toISOString();
}

export function OpportunityFunnel() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [profiles, setProfiles] = useState<CapabilityProfile[]>([]);
  const [products, setProducts] = useState<CapabilityProduct[]>([]);
  const [skills, setSkills] = useState<RuntimeSkillBrief[]>([]);
  const [period, setPeriod] = useState<Period>("90D");
  const [industry, setIndustry] = useState("");
  const [profileId, setProfileId] = useState("");
  const [productId, setProductId] = useState("");
  const [skillName, setSkillName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([listCapabilityProfiles(false), fetchRuntimeSkills()])
      .then(([profileItems, skillItems]) => {
        setProfiles(profileItems.filter((item) => item.status === "ACTIVE"));
        setSkills(skillItems ?? []);
      })
      .catch(() => setError("筛选项加载失败，可先使用时间和行业筛选。"));
  }, []);

  useEffect(() => {
    setProductId("");
    if (!profileId) {
      setProducts([]);
      return;
    }
    listCapabilityProducts(profileId)
      .then((items) => setProducts(items.filter((item) => item.status === "ACTIVE")))
      .catch(() => setProducts([]));
  }, [profileId]);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    const start = periodStart(period);
    if (start) params.set("start_at", start);
    params.set("end_at", new Date().toISOString());
    if (industry.trim()) params.set("industry", industry.trim());
    if (profileId) params.set("capability_profile_id", profileId);
    if (productId) params.set("product_id", productId);
    if (skillName) params.set("root_skill_name", skillName);
    return params.toString();
  }, [industry, period, productId, profileId, skillName]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await authenticatedFetch(`/api/watchlist/dashboard?${query}`);
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(typeof body?.detail === "string" ? body.detail : "经营仪表盘加载失败");
      }
      setMetrics(await response.json() as DashboardMetrics);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "经营仪表盘加载失败");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => { void load(); }, [load]);

  const stages = metrics?.funnel ?? [];
  const mainStages = stages.filter((item) => item.key !== "GX");
  const gx = stages.find((item) => item.key === "GX");
  const maxCount = Math.max(1, ...mainStages.map((item) => item.count));

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-neutral-950/10 bg-white/80 p-5 shadow-[var(--shadow-panel)] sm:p-6">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <label className="text-sm font-medium text-neutral-700">
            研究队列时间
            <select value={period} onChange={(event) => setPeriod(event.target.value as Period)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2.5">
              <option value="30D">最近 30 天</option>
              <option value="90D">最近 90 天</option>
              <option value="YEAR">本年度</option>
              <option value="ALL">全部时间</option>
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            客户行业
            <input value={industry} onChange={(event) => setIndustry(event.target.value)} placeholder="精确行业名称" className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2.5" />
          </label>
          <label className="text-sm font-medium text-neutral-700">
            能力档案
            <select value={profileId} onChange={(event) => setProfileId(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2.5">
              <option value="">全部档案</option>
              {profiles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            匹配产品
            <select value={productId} disabled={!profileId} onChange={(event) => setProductId(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2.5 disabled:bg-neutral-100">
              <option value="">{profileId ? "全部产品" : "请先选择档案"}</option>
              {products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            根 Skill
            <select value={skillName} onChange={(event) => setSkillName(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2.5">
              <option value="">全部 Skill</option>
              {skills.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
            </select>
          </label>
        </div>
        <div className="mt-4 flex items-center justify-between gap-3 text-xs text-neutral-500">
          <p>时间筛选以研究任务创建时间定义队列，后续阶段累计追踪，不混用事件流量口径。</p>
          <button type="button" onClick={() => void load()} className="shrink-0 rounded-full border border-neutral-300 px-3 py-1.5 font-medium text-neutral-800 hover:border-neutral-950">刷新</button>
        </div>
      </section>

      {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {loading && <p className="rounded-lg border border-dashed border-neutral-300 p-12 text-center text-sm text-neutral-500">正在汇总经营事实账本…</p>}

      {!loading && metrics && (
        <>
          <section className="rounded-xl border border-neutral-950/10 bg-neutral-950 p-5 text-white shadow-[var(--shadow-panel)] sm:p-6">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div><p className="text-xs font-semibold tracking-[0.18em] text-lime-300">OPPORTUNITY FUNNEL</p><h2 className="mt-1 text-2xl font-semibold">证据化商机推进漏斗</h2></div>
              <p className="text-xs text-neutral-400">更新于 {new Date(metrics.generated_at).toLocaleString("zh-CN", { hour12: false })}</p>
            </div>
            <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
              {mainStages.map((stage) => (
                <article key={stage.key} className="rounded-lg border border-white/10 bg-white/5 p-4">
                  <div className="flex items-baseline justify-between gap-2"><p className="text-sm text-neutral-300">{stage.label}</p><strong className="text-2xl">{stage.count}</strong></div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-lime-300" style={{ width: `${Math.max(stage.count ? 6 : 0, stage.count / maxCount * 100)}%` }} /></div>
                  <p className="mt-2 text-xs text-neutral-400">过门率 {percent(stage.conversion_from_previous)}</p>
                </article>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-300/20 bg-amber-300/10 p-4">
              <div><p className="text-sm font-medium text-amber-100">GX：证据支持不建议推进</p><p className="mt-1 text-xs text-amber-100/70">这是独立终态，不参与递进过门率计算。</p></div>
              <strong className="text-3xl text-amber-200">{gx?.count ?? 0}</strong>
            </div>
          </section>

          <section className="grid gap-4 lg:grid-cols-3">
            <article className="rounded-xl border border-neutral-950/10 bg-white p-5 shadow-[var(--shadow-panel)]">
              <h3 className="font-semibold text-neutral-950">人工验证结果</h3>
              <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-lg bg-lime-50 p-3"><p className="text-neutral-600">信号接受率</p><strong className="mt-1 block text-xl text-neutral-950">{percent(metrics.outcomes.signal_acceptance_rate)}</strong><p className="text-xs text-neutral-500">{metrics.outcomes.signal_accepted} 接受 / {metrics.outcomes.signal_rejected} 拒绝</p></div>
                <div className="rounded-lg bg-cyan-50 p-3"><p className="text-neutral-600">客户验证率</p><strong className="mt-1 block text-xl text-neutral-950">{percent(metrics.outcomes.customer_validation_rate)}</strong><p className="text-xs text-neutral-500">{metrics.outcomes.customer_validated} 通过 / {metrics.outcomes.customer_invalidated} 否定</p></div>
                <div className="rounded-lg bg-neutral-100 p-3"><p className="text-neutral-600">暂无商机</p><strong className="mt-1 block text-xl">{metrics.outcomes.no_opportunity}</strong></div>
                <div className="rounded-lg bg-red-50 p-3"><p className="text-neutral-600">主体误判</p><strong className="mt-1 block text-xl text-red-800">{metrics.outcomes.identification_error}</strong></div>
              </div>
            </article>

            <article className="rounded-xl border border-neutral-950/10 bg-white p-5 shadow-[var(--shadow-panel)]">
              <h3 className="font-semibold text-neutral-950">确认金额</h3>
              <p className="mt-1 text-xs text-neutral-500">仅客户确认或 CRM 导入；不同币种不合并。</p>
              <div className="mt-4 space-y-3">
                {metrics.amounts.by_currency.length === 0 ? <p className="rounded-lg bg-neutral-100 p-4 text-sm text-neutral-600">尚无已确认金额</p> : metrics.amounts.by_currency.map((item) => (
                  <div key={item.currency} className="rounded-lg bg-neutral-100 p-3"><p className="text-xs font-semibold text-neutral-500">{item.currency}</p><p className="mt-1 text-sm">管道 <strong>{number(item.confirmed_pipeline_amount)}</strong> · 成交 <strong>{number(item.confirmed_won_amount)}</strong></p></div>
                ))}
                <p className="text-sm text-neutral-600">未录入或仅为估算：<strong className="text-neutral-950">{metrics.amounts.missing_or_unconfirmed_count}</strong> 个商机</p>
              </div>
            </article>

            <article className="rounded-xl border border-neutral-950/10 bg-white p-5 shadow-[var(--shadow-panel)]">
              <h3 className="font-semibold text-neutral-950">执行成本与时效</h3>
              <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-neutral-500">外部调用</dt><dd className="mt-1 text-xl font-semibold">{metrics.execution.external_call_count}</dd></div>
                <div><dt className="text-neutral-500">总 Token</dt><dd className="mt-1 text-xl font-semibold">{number(metrics.execution.input_tokens + metrics.execution.output_tokens)}</dd></div>
                <div><dt className="text-neutral-500">平均研究耗时</dt><dd className="mt-1 font-medium">{duration(metrics.execution.average_research_duration_seconds)}</dd></div>
                <div><dt className="text-neutral-500">节省工时</dt><dd className="mt-1 font-medium">{metrics.execution.saved_labor_hours_status === "AVAILABLE" ? `${metrics.execution.saved_labor_hours} 小时` : "未配置人工基线"}</dd></div>
              </dl>
              <div className="mt-4 border-t border-neutral-200 pt-3 text-sm text-neutral-600">
                {metrics.execution.settled_costs.length === 0 ? "暂无已结算调用费用" : metrics.execution.settled_costs.map((item) => <p key={item.currency}>已结算 {item.currency} <strong className="text-neutral-950">{number(item.settled_amount)}</strong></p>)}
              </div>
            </article>
          </section>

          <section className="rounded-xl border border-neutral-950/10 bg-white p-5 shadow-[var(--shadow-panel)] sm:p-6">
            <h3 className="font-semibold text-neutral-950">阶段停留时间</h3>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {metrics.dwell_times.map((item) => <div key={item.key} className="rounded-lg border border-neutral-200 p-4"><p className="text-sm text-neutral-600">{item.label}</p><strong className="mt-2 block text-xl text-neutral-950">{duration(item.average_seconds)}</strong><p className="mt-1 text-xs text-neutral-500">{item.sample_count} 个有效样本</p></div>)}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
