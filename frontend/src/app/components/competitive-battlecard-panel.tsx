"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import {
  createCompetitiveBattlecard,
  createOpportunityCompetitor,
  dismissOpportunityCompetitor,
  listCompetitiveBattlecards,
  listOpportunityCompetitors,
  proposeCompetitiveBattlecardDraft,
  type CompetitiveBattlecard,
  type CompetitiveBattlecardDraft,
  type CompetitiveBattlecardPayload,
  type OpportunityCompetitor,
} from "@/lib/opportunities";
import {
  listCapabilityDocuments,
  listCapabilityProfiles,
  type CapabilityKnowledgeDocument,
} from "@/lib/capabilities";
import type { WorkbenchClaim } from "@/lib/target-accounts";


type Props = {
  opportunityId: string;
  claims: WorkbenchClaim[];
  onError: (message: string) => void;
};

const TYPE_LABEL: Record<OpportunityCompetitor["competitor_type"], string> = {
  COMMERCIAL_VENDOR: "商业竞品",
  INCUMBENT_VENDOR: "现有供应商",
  CUSTOMER_SELF_BUILD: "客户自研",
  STATUS_QUO: "维持现状",
  DELAY: "延期",
  NO_INVESTMENT: "不投资",
};

export function CompetitiveBattlecardPanel({ opportunityId, claims, onError }: Props) {
  const [competitors, setCompetitors] = useState<OpportunityCompetitor[]>([]);
  const [cards, setCards] = useState<Record<string, CompetitiveBattlecard | null>>({});
  const [documents, setDocuments] = useState<CapabilityKnowledgeDocument[]>([]);
  const [adding, setAdding] = useState(false);
  const [type, setType] = useState<OpportunityCompetitor["competitor_type"]>("STATUS_QUO");
  const [name, setName] = useState("");
  const [truth, setTruth] = useState<OpportunityCompetitor["truth_status"]>("SALES_JUDGMENT");
  const [claimId, setClaimId] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [switchingCost, setSwitchingCost] = useState("");
  const [strength, setStrength] = useState("");
  const [strengthClaimId, setStrengthClaimId] = useState("");
  const [differentiator, setDifferentiator] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [question, setQuestion] = useState("");
  const [model, setModel] = useState("");
  const [draft, setDraft] = useState<CompetitiveBattlecardDraft | null>(null);
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    const items = await listOpportunityCompetitors(opportunityId);
    setCompetitors(items);
    const entries = await Promise.all(items.map(async (item) => {
      const versions = await listCompetitiveBattlecards(item.id);
      return [item.id, versions[0] ?? null] as const;
    }));
    setCards(Object.fromEntries(entries));
  }, [opportunityId]);

  useEffect(() => {
    void refresh().catch((error) => onError(error instanceof Error ? error.message : "竞争信息加载失败"));
    void listCapabilityProfiles()
      .then(async (profiles) => {
        const profile = profiles.find((item) => item.is_default) ?? profiles[0];
        if (!profile) return [];
        return listCapabilityDocuments(profile.id);
      })
      .then((items) => setDocuments(items.filter((item) => item.status === "READY")))
      .catch((error) => onError(error instanceof Error ? error.message : "内部能力资料加载失败"));
  }, [onError, refresh]);

  const createCompetitor = async () => {
    if ((type === "COMMERCIAL_VENDOR" || type === "INCUMBENT_VENDOR") && !name.trim()) {
      onError("商业竞品或现有供应商必须填写名称");
      return;
    }
    if (truth !== "SALES_JUDGMENT" && !claimId) {
      onError("公开证据或客户确认的竞争判断必须选择 Claim");
      return;
    }
    setSubmitting(true);
    try {
      await createOpportunityCompetitor(opportunityId, {
        competitor_type: type,
        truth_status: truth,
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(claimId ? { source_claim_id: claimId } : {}),
      });
      setAdding(false);
      setName("");
      setClaimId("");
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : "竞争对象创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const saveBattlecard = async () => {
    if (!selectedId) return;
    if (strength.trim() && !strengthClaimId) {
      onError("竞品优势属于客户侧判断，必须选择 Claim");
      return;
    }
    if (differentiator.trim() && !documentId) {
      onError("我方差异化必须选择内部能力资料");
      return;
    }
    setSubmitting(true);
    try {
      const base = draft?.battlecard ?? {};
      const payload: CompetitiveBattlecardPayload = {
        ...base,
        current_contract: base.current_contract ?? { status: "UNKNOWN" },
        switching_cost_assessment: switchingCost.trim(),
        competitor_strengths: strength.trim() ? [{
          text: strength.trim(),
          source_domain: base.competitor_strengths?.[0]?.source_id === strengthClaimId
            ? base.competitor_strengths[0].source_domain
            : "external",
          source_id: strengthClaimId,
        }, ...(base.competitor_strengths?.slice(1) ?? [])] : [],
        our_differentiators: differentiator.trim() ? [{
          text: differentiator.trim(),
          source_domain: "internal",
          source_id: documentId,
        }, ...(base.our_differentiators?.slice(1) ?? [])] : [],
        discovery_questions: question.trim()
          ? [question.trim(), ...(base.discovery_questions?.slice(1) ?? [])]
          : [],
      };
      await createCompetitiveBattlecard(selectedId, payload);
      setSelectedId("");
      setSwitchingCost("");
      setStrength("");
      setStrengthClaimId("");
      setDifferentiator("");
      setDocumentId("");
      setQuestion("");
      setDraft(null);
      await refresh();
    } catch (error) {
      onError(error instanceof Error ? error.message : "竞争作战卡保存失败");
    } finally {
      setSubmitting(false);
    }
  };

  const generateDraft = async () => {
    if (!selectedId) return;
    if (!strengthClaimId && !documentId) {
      onError("请至少选择一个客户 Claim 或一份内部能力资料作为智能体上下文");
      return;
    }
    setGenerating(true);
    try {
      const result = await proposeCompetitiveBattlecardDraft(selectedId, {
        claim_ids: strengthClaimId ? [strengthClaimId] : [],
        internal_document_ids: documentId ? [documentId] : [],
        ...(model.trim() ? { model: model.trim() } : {}),
      });
      const firstStrength = result.battlecard.competitor_strengths?.[0];
      const firstDifferentiator = result.battlecard.our_differentiators?.[0];
      setDraft(result);
      setSwitchingCost(result.battlecard.switching_cost_assessment ?? "");
      setStrength(firstStrength?.text ?? "");
      setStrengthClaimId(firstStrength?.source_id ?? strengthClaimId);
      setDifferentiator(firstDifferentiator?.text ?? "");
      setDocumentId(firstDifferentiator?.source_id ?? documentId);
      setQuestion(result.battlecard.discovery_questions?.[0] ?? "");
    } catch (error) {
      onError(error instanceof Error ? error.message : "竞争作战卡草案生成失败");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <section className="mt-4 border-t border-neutral-200 pt-4">
      <div className="flex items-center justify-between gap-3">
        <div><p className="text-sm font-semibold text-neutral-900">竞争作战</p><p className="mt-1 text-xs text-neutral-500">竞品之外，还必须评估维持现状、自研、延期和不投资。</p></div>
        <Button type="button" size="sm" variant="secondary" onClick={() => setAdding((value) => !value)}>新增竞争对象</Button>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {competitors.map((item) => {
          const card = cards[item.id];
          return (
            <article key={item.id} className="rounded-lg border border-neutral-200 p-3">
              <div className="flex items-start justify-between gap-2">
                <div><p className="text-sm font-semibold text-neutral-900">{item.name || TYPE_LABEL[item.competitor_type]}</p><p className="text-xs text-neutral-500">{TYPE_LABEL[item.competitor_type]}</p></div>
                <StatusBadge status={item.truth_status} label={item.truth_status} />
              </div>
              {card ? <p className="mt-2 text-xs text-neutral-600">最新作战卡 V{card.version_no} · {card.discovery_questions.length} 个发现问题</p> : <p className="mt-2 text-xs text-neutral-500">尚无作战卡</p>}
              <div className="mt-2 flex gap-3 text-xs">
                <button type="button" onClick={() => setSelectedId(item.id)} className="font-medium underline underline-offset-4">{card ? "更新作战卡" : "创建作战卡"}</button>
                <button type="button" onClick={() => void dismissOpportunityCompetitor(item.id).then(refresh).catch((error) => onError(error instanceof Error ? error.message : "排除失败"))} className="font-medium text-red-700 underline underline-offset-4">排除</button>
              </div>
            </article>
          );
        })}
      </div>

      {adding && (
        <div className="mt-3 grid gap-3 rounded-lg bg-neutral-50 p-3 md:grid-cols-2">
          <label className="text-xs font-medium text-neutral-600">竞争类型<select value={type} onChange={(event) => setType(event.target.value as typeof type)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm">{Object.entries(TYPE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="text-xs font-medium text-neutral-600">名称<input value={name} onChange={(event) => setName(event.target.value)} disabled={!['COMMERCIAL_VENDOR', 'INCUMBENT_VENDOR'].includes(type)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm disabled:bg-neutral-100" /></label>
          <label className="text-xs font-medium text-neutral-600">真实性<select value={truth} onChange={(event) => { setTruth(event.target.value as typeof truth); setClaimId(""); }} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm"><option value="SALES_JUDGMENT">销售判断</option><option value="PUBLIC_EVIDENCE">公开证据</option><option value="CUSTOMER_CONFIRMED">客户确认</option></select></label>
          <label className="text-xs font-medium text-neutral-600">来源 Claim<select value={claimId} disabled={truth === "SALES_JUDGMENT"} onChange={(event) => setClaimId(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm disabled:bg-neutral-100"><option value="">请选择</option>{claims.filter((claim) => truth !== "CUSTOMER_CONFIRMED" || claim.status === "CUSTOMER_CONFIRMED").map((claim) => <option key={claim.id} value={claim.id}>{claim.status} · {claim.claim_text}</option>)}</select></label>
          <div className="flex gap-2 md:col-span-2"><Button type="button" size="sm" isLoading={submitting} onClick={() => void createCompetitor()}>保存</Button><Button type="button" size="sm" variant="ghost" onClick={() => setAdding(false)}>取消</Button></div>
        </div>
      )}

      {selectedId && (
        <div className="mt-3 grid gap-3 rounded-lg bg-neutral-50 p-3 md:grid-cols-2">
          <div className="md:col-span-2 rounded-lg border border-blue-200 bg-blue-50 p-3">
            <p className="text-xs font-semibold text-blue-900">智能体只生成待审草案，不会自动发布或覆盖已有版本</p>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="受限资料需填写已批准模型名称" className="min-w-0 flex-1 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm" />
              <Button type="button" size="sm" variant="secondary" isLoading={generating} onClick={() => void generateDraft()}>AI 生成草案</Button>
            </div>
            {draft && <div className="mt-2 text-xs text-blue-900"><p>{draft.summary}</p>{draft.uncertainties.length > 0 && <p className="mt-1">待确认：{draft.uncertainties.join("；")}</p>}<p className="mt-1 text-blue-700">请逐项检查并点击“保存作战卡版本”后才会生效。</p></div>}
          </div>
          <label className="text-xs font-medium text-neutral-600 md:col-span-2">切换成本判断<textarea value={switchingCost} onChange={(event) => setSwitchingCost(event.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm" /></label>
          <label className="text-xs font-medium text-neutral-600">竞品/现状优势<input value={strength} onChange={(event) => setStrength(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm" /></label>
          <label className="text-xs font-medium text-neutral-600">客户侧 Claim<select value={strengthClaimId} onChange={(event) => setStrengthClaimId(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm"><option value="">请选择</option>{claims.map((claim) => <option key={claim.id} value={claim.id}>{claim.status} · {claim.claim_text}</option>)}</select></label>
          <label className="text-xs font-medium text-neutral-600">我方差异化<input value={differentiator} onChange={(event) => setDifferentiator(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm" /></label>
          <label className="text-xs font-medium text-neutral-600">内部能力资料<select value={documentId} onChange={(event) => setDocumentId(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm"><option value="">请选择</option>{documents.map((item) => <option key={item.id} value={item.id}>{item.original_filename} · V{item.version_no}</option>)}</select></label>
          <label className="text-xs font-medium text-neutral-600 md:col-span-2">竞争性发现问题<input value={question} onChange={(event) => setQuestion(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm" /></label>
          <div className="flex gap-2 md:col-span-2"><Button type="button" size="sm" isLoading={submitting} onClick={() => void saveBattlecard()}>保存作战卡版本</Button><Button type="button" size="sm" variant="ghost" onClick={() => { setSelectedId(""); setDraft(null); }}>取消</Button></div>
        </div>
      )}
    </section>
  );
}
