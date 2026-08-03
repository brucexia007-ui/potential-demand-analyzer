"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import {
  changeOpportunityStage,
  listOpportunityHistory,
  type OpportunityStage,
  type OpportunityStageHistory,
} from "@/lib/opportunities";
import type { WorkbenchOpportunity } from "@/lib/target-accounts";
import type { WorkbenchClaim } from "@/lib/target-accounts";
import { CompetitiveBattlecardPanel } from "@/app/components/competitive-battlecard-panel";
import { ValueHypothesisPanel } from "@/app/components/value-hypothesis-panel";


type Props = {
  opportunity: WorkbenchOpportunity;
  claims: WorkbenchClaim[];
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
};

const TRANSITIONS: Partial<Record<OpportunityStage, OpportunityStage[]>> = {
  QUALIFICATION: ["DISCOVERY", "CANCELLED"],
  DISCOVERY: ["SOLUTION_SHAPING", "CANCELLED"],
  SOLUTION_SHAPING: ["PROPOSAL", "TENDER", "CANCELLED"],
  PROPOSAL: ["NEGOTIATION", "WON", "LOST", "CANCELLED"],
  TENDER: ["NEGOTIATION", "WON", "LOST", "CANCELLED"],
  NEGOTIATION: ["WON", "LOST", "CANCELLED"],
};

const STAGE_LABEL: Record<OpportunityStage, string> = {
  QUALIFICATION: "资格确认",
  DISCOVERY: "需求发现",
  SOLUTION_SHAPING: "方案塑造",
  PROPOSAL: "方案建议",
  TENDER: "投标采购",
  NEGOTIATION: "商务谈判",
  WON: "赢单",
  LOST: "丢单",
  CANCELLED: "已取消",
};

const dateTime = (value: string) => new Date(value).toLocaleString("zh-CN");

export function FormalOpportunityCard({ opportunity, claims, onChanged, onError }: Props) {
  const currentStage = opportunity.stage as OpportunityStage;
  const transitions = TRANSITIONS[currentStage] ?? [];
  const [toStage, setToStage] = useState<OpportunityStage | "">("");
  const [reason, setReason] = useState("");
  const [closeReason, setCloseReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [history, setHistory] = useState<OpportunityStageHistory[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const submit = async () => {
    if (!toStage || !reason.trim()) {
      onError("请选择下一阶段并填写推进依据");
      return;
    }
    if (toStage === "LOST" && !closeReason.trim()) {
      onError("标记丢单时必须填写丢单原因");
      return;
    }
    setSubmitting(true);
    try {
      await changeOpportunityStage(opportunity.id, {
        to_stage: toStage,
        reason: reason.trim(),
        request_key: crypto.randomUUID(),
        ...(closeReason.trim() ? { close_reason: closeReason.trim() } : {}),
      });
      setToStage("");
      setReason("");
      setCloseReason("");
      setHistory(null);
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "商机阶段推进失败");
    } finally {
      setSubmitting(false);
    }
  };

  const toggleHistory = async () => {
    if (history !== null) {
      setHistory(null);
      return;
    }
    setHistoryLoading(true);
    try {
      setHistory(await listOpportunityHistory(opportunity.id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "商机历史加载失败");
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <article className="rounded-lg border border-neutral-200 p-5" data-testid="formal-opportunity">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-neutral-950">{opportunity.title}</h3>
          <p className="mt-1 text-xs text-neutral-500">
            创建于 {dateTime(opportunity.created_at)} · 成交概率 {Math.round(opportunity.probability * 100)}%
          </p>
        </div>
        <StatusBadge status={opportunity.stage} label={STAGE_LABEL[currentStage]} />
      </div>
      <dl className="mt-4 grid gap-3 text-sm md:grid-cols-3">
        <div><dt className="text-neutral-500">预计金额</dt><dd className="mt-1 text-neutral-900">{opportunity.amount ? `${opportunity.currency} ${opportunity.amount}` : "尚未确认"}</dd></div>
        <div><dt className="text-neutral-500">金额来源</dt><dd className="mt-1 text-neutral-900">{opportunity.amount_source}</dd></div>
        <div><dt className="text-neutral-500">预计成交</dt><dd className="mt-1 text-neutral-900">{opportunity.expected_close_date || "尚未确认"}</dd></div>
      </dl>
      {opportunity.close_reason && <p className="mt-3 rounded-lg bg-neutral-100 p-3 text-sm text-neutral-700">关闭原因：{opportunity.close_reason}</p>}

      {transitions.length > 0 && (
        <div className="mt-4 grid gap-3 rounded-lg bg-neutral-50 p-4 md:grid-cols-2">
          <label className="text-sm font-medium text-neutral-700">
            下一阶段
            <select value={toStage} onChange={(event) => setToStage(event.target.value as OpportunityStage)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
              <option value="">请选择</option>
              {transitions.map((stage) => <option key={stage} value={stage}>{STAGE_LABEL[stage]}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            推进依据
            <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
          </label>
          {(toStage === "LOST" || toStage === "CANCELLED") && (
            <label className="text-sm font-medium text-neutral-700 md:col-span-2">
              关闭原因{toStage === "LOST" ? " *" : ""}
              <textarea value={closeReason} onChange={(event) => setCloseReason(event.target.value)} rows={2} maxLength={2000} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
            </label>
          )}
          <div className="md:col-span-2"><Button type="button" size="sm" isLoading={submitting} onClick={() => void submit()}>确认推进</Button></div>
        </div>
      )}

      <button type="button" onClick={() => void toggleHistory()} disabled={historyLoading} className="mt-4 text-sm font-medium text-neutral-700 underline decoration-neutral-300 underline-offset-4">
        {historyLoading ? "加载中…" : history === null ? "查看阶段历史" : "收起阶段历史"}
      </button>
      <Link href={`/opportunities/${opportunity.id}`} className="ml-4 text-sm font-medium text-neutral-950 underline decoration-neutral-300 underline-offset-4">
        记录业务结果
      </Link>
      {history !== null && (
        <ol className="mt-3 space-y-2 border-l border-neutral-200 pl-4">
          {history.map((item) => (
            <li key={item.id} className="text-sm text-neutral-700">
              <strong>{item.from_stage ? STAGE_LABEL[item.from_stage] : "创建商机"} → {STAGE_LABEL[item.to_stage]}</strong>
              <span className="ml-2 text-xs text-neutral-500">{dateTime(item.created_at)}</span>
              <p className="mt-1">{item.reason}</p>
            </li>
          ))}
        </ol>
      )}
      <CompetitiveBattlecardPanel opportunityId={opportunity.id} claims={claims} onError={onError} />
      <ValueHypothesisPanel opportunityId={opportunity.id} claims={claims} onError={onError} />
    </article>
  );
}
