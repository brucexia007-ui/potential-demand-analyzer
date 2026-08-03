"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { BusinessFeedback } from "@/app/components/business-feedback";
import { Card } from "@/components/ui/card";
import { PageHeader, PageShell, StatusBadge } from "@/components/ui/workspace";
import { useToast } from "@/components/ui/toast";
import {
  getFormalOpportunity,
  listOpportunityHistory,
  type FormalOpportunity,
  type OpportunityStageHistory,
} from "@/lib/opportunities";


export default function OpportunityDetailPage() {
  const params = useParams<{ id: string }>();
  const opportunityId = useMemo(() => String(params?.id ?? ""), [params]);
  const [opportunity, setOpportunity] = useState<FormalOpportunity | null>(null);
  const [history, setHistory] = useState<OpportunityStageHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { error: toastError } = useToast();

  useEffect(() => {
    if (!opportunityId) return;
    let active = true;
    Promise.all([getFormalOpportunity(opportunityId), listOpportunityHistory(opportunityId)])
      .then(([item, transitions]) => {
        if (!active) return;
        setOpportunity(item);
        setHistory(transitions);
        setLoadError(null);
      })
      .catch((error) => {
        if (!active) return;
        const message = error instanceof Error ? error.message : "正式商机加载失败";
        setLoadError(message);
        toastError(message);
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [opportunityId, toastError]);

  if (loading) return <PageShell><p className="py-24 text-center text-sm text-neutral-500">正在加载正式商机…</p></PageShell>;
  if (loadError || !opportunity) {
    return (
      <PageShell>
        <Card variant="bordered" padding="lg" className="text-center">
          <h1 className="text-xl font-semibold text-neutral-950">无法打开正式商机</h1>
          <p className="mt-2 text-sm text-neutral-600">{loadError || "正式商机不存在"}</p>
          <Link href="/customers" className="mt-4 inline-flex text-sm font-medium underline">返回客户列表</Link>
        </Card>
      </PageShell>
    );
  }

  return (
    <PageShell data-testid="opportunity-detail">
      <PageHeader
        eyebrow="FORMAL OPPORTUNITY"
        title={opportunity.title}
        description="正式商机阶段与业务结果必须由销售人工确认；AI 研究只提供证据和建议。"
        meta={<StatusBadge status={opportunity.stage} label={opportunity.stage} />}
        action={<Link href={`/customers/${opportunity.target_account_id}`} className="inline-flex rounded-full border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-950">返回客户工作台</Link>}
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card variant="bordered" padding="lg">
          <BusinessFeedback opportunity={opportunity} history={history} onError={toastError} />
        </Card>
        <aside className="space-y-6">
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-950">商机概览</h2>
            <dl className="mt-4 space-y-3 text-sm">
              <div><dt className="text-neutral-500">当前阶段</dt><dd className="mt-1 text-neutral-950">{opportunity.stage}</dd></div>
              <div><dt className="text-neutral-500">金额</dt><dd className="mt-1 text-neutral-950">{opportunity.amount === null ? "未录入" : `${opportunity.amount} ${opportunity.currency}`}</dd></div>
              <div><dt className="text-neutral-500">金额来源</dt><dd className="mt-1 text-neutral-950">{opportunity.amount_source}</dd></div>
              <div><dt className="text-neutral-500">预计成交</dt><dd className="mt-1 text-neutral-950">{opportunity.expected_close_date || "未设置"}</dd></div>
              <div><dt className="text-neutral-500">概率</dt><dd className="mt-1 text-neutral-950">{Math.round(opportunity.probability * 100)}%</dd></div>
            </dl>
          </Card>
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-950">阶段历史</h2>
            <div className="mt-4 space-y-3">
              {history.map((item) => (
                <article key={item.id} className="border-l-2 border-neutral-200 pl-3">
                  <p className="text-sm font-medium text-neutral-950">{item.from_stage || "商机创建"} → {item.to_stage}</p>
                  <p className="mt-1 text-xs text-neutral-500">{item.reason} · {new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</p>
                </article>
              ))}
            </div>
          </Card>
        </aside>
      </div>
    </PageShell>
  );
}
