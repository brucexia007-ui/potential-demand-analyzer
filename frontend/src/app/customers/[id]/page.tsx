"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Card } from "@/components/ui/card";
import { DataPanel, PageHeader, PageShell, StatusBadge } from "@/components/ui/workspace";
import { useToast } from "@/components/ui/toast";
import { ReportConversation } from "@/app/components/report-conversation";
import { OpportunityHypothesisCard } from "@/app/components/opportunity-hypothesis-card";
import { FormalOpportunityCard } from "@/app/components/formal-opportunity-card";
import { StakeholderMap } from "@/app/components/stakeholder-map";
import { OpportunityResearchPlan } from "@/app/components/opportunity-research-plan";
import { BusinessExportDialog } from "@/app/components/business-export-dialog";
import { CustomerRadar } from "@/app/components/customer-radar";
import {
  listOpportunityStakeholders,
  listQualificationFrameworks,
  type OpportunityStakeholder,
  type QualificationFramework,
} from "@/lib/opportunities";
import {
  getTargetAccountWorkbench,
  type TargetAccountWorkbench,
} from "@/lib/target-accounts";

const percent = (value: number) => `${Math.round(value * 100)}%`;
const date = (value: string | null) => value ? new Date(value).toLocaleDateString("zh-CN") : "未设置";

function EmptyState({ children }: { children: string }) {
  return <p className="rounded-lg border border-dashed border-neutral-300 px-4 py-8 text-center text-sm text-neutral-500">{children}</p>;
}

export default function CustomerWorkbenchPage() {
  const params = useParams<{ id: string }>();
  const accountId = useMemo(() => String(params?.id ?? ""), [params]);
  const [workbench, setWorkbench] = useState<TargetAccountWorkbench | null>(null);
  const [qualificationFrameworks, setQualificationFrameworks] = useState<QualificationFramework[]>([]);
  const [stakeholders, setStakeholders] = useState<OpportunityStakeholder[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const { error: toastError } = useToast();

  useEffect(() => {
    if (!accountId) return;
    let active = true;
    setLoading(true);
    Promise.all([
      getTargetAccountWorkbench(accountId),
      listQualificationFrameworks(),
      listOpportunityStakeholders(accountId),
    ])
      .then(([value, frameworks, stakeholderItems]) => {
        if (!active) return;
        setWorkbench(value);
        setQualificationFrameworks(frameworks);
        setStakeholders(stakeholderItems);
        setLoadError(null);
      })
      .catch((error) => {
        if (!active) return;
        const message = error instanceof Error ? error.message : "客户工作台加载失败";
        setLoadError(message);
        toastError(message);
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [accountId, toastError]);

  if (loading) {
    return <PageShell><div className="py-24 text-center text-sm text-neutral-500">正在加载客户研究资产…</div></PageShell>;
  }
  if (loadError || !workbench) {
    return (
      <PageShell>
        <Card variant="bordered" padding="lg" className="text-center">
          <h1 className="text-xl font-semibold text-neutral-950">无法打开客户工作台</h1>
          <p className="mt-2 text-sm text-neutral-600">{loadError || "目标企业不存在"}</p>
          <Link href="/customers" className="mt-5 inline-flex rounded-full bg-neutral-950 px-4 py-2 text-sm font-medium text-white">返回目标企业</Link>
        </Card>
      </PageShell>
    );
  }

  const { account, counts, tasks, claims, latest_gate: gate, hypotheses, opportunities } = workbench;
  const name = account.official_name || account.input_name;
  const reportTasks = tasks.filter((task) => task.report_id !== null);
  const activeReport = reportTasks.find((task) => task.report_id === selectedReportId) ?? reportTasks[0];

  return (
    <PageShell data-testid="customer-workbench">
      <PageHeader
        eyebrow="ACCOUNT WORKBENCH"
        title={name}
        description="围绕同一客户持续维护研究证据、判断结论、商机假设和下一步行动；报告是其中一种可追溯资产。"
        meta={
          <div className="flex flex-wrap items-center gap-2 text-xs text-neutral-500">
            <StatusBadge status={account.status} label={account.status === "CONFIRMED" ? "主体已确认" : "主体待消歧"} />
            <span>{account.industry || "行业未填写"}</span><span>·</span><span>{account.region || "地区未填写"}</span>
          </div>
        }
        action={
          <>
            <Link href="/customers" className="inline-flex rounded-full border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-950">返回客户列表</Link>
            <Link href="/#task-form" className="inline-flex rounded-full border border-neutral-950 bg-neutral-950 px-4 py-2 text-sm font-medium text-white">发起新研究</Link>
          </>
        }
      />

      <section className="grid grid-cols-2 gap-3 md:grid-cols-6" aria-label="客户资产统计">
        <DataPanel label="研究任务" value={counts.tasks} detail="持续归集" />
        <DataPanel label="关键结论" value={counts.claims} detail="Claim Registry" tone="cyan" />
        <DataPanel label="资格判断" value={counts.gate_decisions} detail="Gate 决策" />
        <DataPanel label="商机假设" value={counts.hypotheses} detail="尚需验证" tone="lime" />
        <DataPanel label="正式商机" value={counts.opportunities} detail="阶段推进" tone="success" />
        <DataPanel label="待办行动" value={counts.pending_actions} detail="待推进、进行中或失败待处理" tone={counts.pending_actions > 0 ? "warning" : "success"} />
      </section>

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
        <div className="space-y-6">
          <Card variant="bordered" padding="lg">
            <CustomerRadar accountId={accountId} accountStatus={account.status} onError={toastError} />
          </Card>

          <Card variant="bordered" padding="lg">
            <OpportunityResearchPlan accountId={accountId} accountName={name} onError={toastError} />
          </Card>

          <Card variant="bordered" padding="lg">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">FORMAL OPPORTUNITIES</p>
            <h2 className="mt-1 text-xl font-semibold text-neutral-950">正式商机与阶段推进</h2>
            <p className="mt-1 text-sm text-neutral-500">仅展示经销售接受、客户确认、G5 与资格卡共同验证后创建的正式商机。</p>
            <div className="mt-4">
              {opportunities.length === 0 ? <EmptyState>尚无正式商机。先验证商机假设并通过资格卡，再由销售人工创建。</EmptyState> : (
                <div className="space-y-4">
                  {opportunities.map((item) => (
                    <FormalOpportunityCard
                      key={item.id}
                      opportunity={item}
                      claims={claims}
                      onError={toastError}
                      onChanged={async () => setWorkbench(await getTargetAccountWorkbench(accountId))}
                    />
                  ))}
                </div>
              )}
            </div>
          </Card>

          <Card variant="bordered" padding="lg">
            <StakeholderMap
              accountId={accountId}
              stakeholders={stakeholders}
              opportunities={opportunities}
              claims={claims}
              onError={toastError}
              onChanged={async () => {
                const [nextWorkbench, nextStakeholders] = await Promise.all([
                  getTargetAccountWorkbench(accountId),
                  listOpportunityStakeholders(accountId),
                ]);
                setWorkbench(nextWorkbench);
                setStakeholders(nextStakeholders);
              }}
            />
          </Card>

          <Card variant="bordered" padding="lg">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">OPPORTUNITY HYPOTHESES</p>
            <h2 className="mt-1 text-xl font-semibold text-neutral-950">商机假设与行动</h2>
            <div className="mt-4">
              {hypotheses.length === 0 ? <EmptyState>暂无通过资格门的商机假设；这也可能是正确的研究结论。</EmptyState> : (
                <div className="space-y-4">
                  {hypotheses.map((item) => (
                    <OpportunityHypothesisCard
                      key={item.id}
                      hypothesis={item}
                      claims={claims}
                      frameworks={qualificationFrameworks}
                      onError={toastError}
                      onChanged={async () => setWorkbench(await getTargetAccountWorkbench(accountId))}
                    />
                  ))}
                </div>
              )}
            </div>
          </Card>

          {activeReport?.report_id ? (
            <section>
              {reportTasks.length > 1 && (
                <label className="mb-3 block text-sm font-medium text-neutral-700">
                  选择正式报告
                  <select
                    value={activeReport.report_id}
                    onChange={(event) => setSelectedReportId(event.target.value)}
                    className="ml-3 rounded-lg border border-neutral-300 bg-white px-3 py-2"
                  >
                    {reportTasks.map((task) => (
                      <option key={task.report_id} value={task.report_id ?? ""}>
                        {task.demand_direction} · V{task.report_version_no ?? "-"} · {date(task.updated_at)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <ReportConversation
                key={activeReport.report_id}
                reportId={activeReport.report_id}
                onReportAccepted={async () => {
                  setWorkbench(await getTargetAccountWorkbench(accountId));
                }}
                onError={toastError}
              />
            </section>
          ) : (
            <Card variant="bordered" padding="lg">
              <h2 className="text-xl font-semibold text-neutral-950">报告智能体</h2>
              <div className="mt-4"><EmptyState>形成首个正式报告版本后，可在客户工作台继续追问、补充研究和审阅修订草案。</EmptyState></div>
            </Card>
          )}

          <Card variant="bordered" padding="lg">
            <h2 className="text-xl font-semibold text-neutral-950">关键结论</h2>
            <p className="mt-1 text-sm text-neutral-500">同一 Claim 被报告、问答、产品匹配和商机判断共同引用。</p>
            <div className="mt-4 space-y-3">
              {claims.length === 0 ? <EmptyState>暂无结构化 Claim。</EmptyState> : claims.map((claim) => (
                <article key={claim.id} className="rounded-lg border border-neutral-200 p-4">
                  <div className="flex items-start justify-between gap-3"><p className="text-sm leading-6 text-neutral-950">{claim.claim_text}</p><StatusBadge status={claim.status} label={claim.status} /></div>
                  <p className="mt-2 text-xs text-neutral-500">{claim.claim_type} · {claim.opportunity_effect} · 置信度 {percent(claim.confidence)} · {claim.evidence_count} 条证据</p>
                </article>
              ))}
            </div>
          </Card>
        </div>

        <aside className="space-y-6">
          <Card variant="bordered" padding="lg">
            <BusinessExportDialog accountId={accountId} onError={toastError} />
          </Card>

          <Card variant="bordered" padding="lg">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">LATEST GATE</p>
            <h2 className="mt-1 text-lg font-semibold text-neutral-950">最新资格判断</h2>
            {!gate ? <div className="mt-4"><EmptyState>尚未形成 Gate 决策。</EmptyState></div> : (
              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between"><StatusBadge status={gate.decision} label={gate.decision} /><strong className="text-2xl text-neutral-950">{gate.gate_level}</strong></div>
                <p className="text-sm text-neutral-600">分析截止 {date(gate.analysis_as_of_date)}</p>
                {Array.isArray(gate.summary.reasons) && <ul className="list-disc space-y-1 pl-5 text-sm text-neutral-800">{gate.summary.reasons.map((reason, index) => <li key={index}>{String(reason)}</li>)}</ul>}
              </div>
            )}
          </Card>

          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-950">研究与报告</h2>
            <div className="mt-4 space-y-3">
              {tasks.length === 0 ? <EmptyState>尚未关联研究任务。</EmptyState> : tasks.map((task) => (
                <article key={task.id} className="rounded-lg border border-neutral-200 p-4">
                  <div className="flex items-start justify-between gap-3"><p className="text-sm font-medium text-neutral-950">{task.demand_direction}</p><StatusBadge status={task.observed_state} label={task.observed_state} /></div>
                  <p className="mt-2 text-xs text-neutral-500">创建于 {date(task.created_at)}{task.report_version_no ? ` · 正式报告 V${task.report_version_no}` : " · 尚无正式报告"}</p>
                  {task.latest_product_match && <p className="mt-2 text-xs text-neutral-600">产品匹配：{task.latest_product_match.status} · 推荐分 {task.latest_product_match.recommendation_score.toFixed(1)}/100 · 证据置信度 {percent(task.latest_product_match.evidence_confidence)}</p>}
                  <Link href={`/tasks/${task.id}`} className="mt-3 inline-flex text-sm font-medium text-neutral-950 underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-950">打开研究任务</Link>
                </article>
              ))}
            </div>
          </Card>

          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-950">企业主数据</h2>
            <dl className="mt-4 space-y-3 text-sm">
              <div><dt className="text-neutral-500">输入名称</dt><dd className="mt-1 text-neutral-900">{account.input_name}</dd></div>
              <div><dt className="text-neutral-500">官网</dt><dd className="mt-1 break-all text-neutral-900">{account.website || "未填写"}</dd></div>
              <div><dt className="text-neutral-500">统一信用代码</dt><dd className="mt-1 text-neutral-900">{account.credit_code || "未填写"}</dd></div>
              <div><dt className="text-neutral-500">股票代码</dt><dd className="mt-1 text-neutral-900">{account.stock_code || "未填写"}</dd></div>
            </dl>
          </Card>
        </aside>
      </div>
    </PageShell>
  );
}
