"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import {
  createWinLossReason,
  listBusinessFeedback,
  listWinLossReasons,
  recordBusinessFeedback,
  type BusinessFeedback as BusinessFeedbackRecord,
  type FeedbackType,
  type FormalOpportunity,
  type OpportunityStageHistory,
  type WinLossReason,
} from "@/lib/opportunities";


type Props = {
  opportunity: FormalOpportunity;
  history: OpportunityStageHistory[];
  onError: (message: string) => void;
};

const FEEDBACK_LABEL: Record<FeedbackType, string> = {
  SIGNAL_ACCEPTED: "销售接受信号",
  SIGNAL_REJECTED: "销售拒绝信号",
  CUSTOMER_VALIDATED: "客户确认需求",
  CUSTOMER_INVALIDATED: "客户否定需求",
  STAGE_ADVANCED: "商机阶段已推进",
  WON: "赢单",
  LOST: "丢单",
  NO_OPPORTUNITY: "确认暂无机会",
  IDENTIFICATION_ERROR: "系统识别错误",
};

const REASON_CATEGORY: Partial<Record<FeedbackType, WinLossReason["category"]>> = {
  WON: "WIN",
  LOST: "LOSS",
  NO_OPPORTUNITY: "NO_OPPORTUNITY",
  IDENTIFICATION_ERROR: "IDENTIFICATION_ERROR",
};

export function BusinessFeedback({ opportunity, history, onError }: Props) {
  const [feedbackType, setFeedbackType] = useState<FeedbackType>("SIGNAL_ACCEPTED");
  const [notes, setNotes] = useState("");
  const [detail, setDetail] = useState("");
  const [reasonId, setReasonId] = useState("");
  const [reasons, setReasons] = useState<WinLossReason[]>([]);
  const [records, setRecords] = useState<BusinessFeedbackRecord[]>([]);
  const [reasonCode, setReasonCode] = useState("");
  const [reasonLabel, setReasonLabel] = useState("");
  const [busy, setBusy] = useState(false);

  const latestTransition = history.at(-1);
  const availableTypes = useMemo<FeedbackType[]>(() => {
    const values: FeedbackType[] = ["SIGNAL_ACCEPTED", "CUSTOMER_VALIDATED"];
    if (latestTransition?.from_stage && latestTransition.from_stage !== latestTransition.to_stage) {
      values.push("STAGE_ADVANCED");
    }
    if (opportunity.stage === "WON") values.push("WON");
    if (opportunity.stage === "LOST") values.push("LOST");
    values.push("NO_OPPORTUNITY", "IDENTIFICATION_ERROR");
    return values;
  }, [latestTransition, opportunity.stage]);
  const requiredCategory = REASON_CATEGORY[feedbackType];
  const eligibleReasons = reasons.filter((item) => item.category === requiredCategory);

  const reload = useCallback(async () => {
    const [reasonItems, feedbackItems] = await Promise.all([
      listWinLossReasons(),
      listBusinessFeedback(opportunity.target_account_id),
    ]);
    setReasons(reasonItems);
    setRecords(feedbackItems.filter((item) => item.opportunity_id === opportunity.id));
  }, [opportunity.id, opportunity.target_account_id]);

  useEffect(() => {
    reload().catch((error) => onError(error instanceof Error ? error.message : "业务反馈加载失败"));
  }, [onError, reload]);

  useEffect(() => {
    if (!availableTypes.includes(feedbackType)) setFeedbackType(availableTypes[0]);
    setReasonId("");
  }, [availableTypes, feedbackType]);

  const createReason = async () => {
    if (!requiredCategory || !reasonCode.trim() || !reasonLabel.trim()) {
      onError("请填写原因代码和名称");
      return;
    }
    setBusy(true);
    try {
      const created = await createWinLossReason({
        code: reasonCode.trim().toUpperCase(),
        label: reasonLabel.trim(),
        category: requiredCategory,
      });
      setReasons((items) => [...items, created]);
      setReasonId(created.id);
      setReasonCode("");
      setReasonLabel("");
    } catch (error) {
      onError(error instanceof Error ? error.message : "反馈原因创建失败");
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (requiredCategory && !reasonId) {
      onError("该结果必须选择受治理原因");
      return;
    }
    if (!detail.trim()) {
      onError("请简要说明客户或销售核验结果");
      return;
    }
    const outcome: Record<string, unknown> = { detail: detail.trim() };
    if (feedbackType === "STAGE_ADVANCED" && latestTransition?.from_stage) {
      outcome.from_stage = latestTransition.from_stage;
      outcome.to_stage = opportunity.stage;
    }
    if (feedbackType === "WON" || feedbackType === "LOST") {
      if (opportunity.amount !== null) outcome.amount = opportunity.amount;
      if (opportunity.currency !== null) outcome.currency = opportunity.currency;
    }
    setBusy(true);
    try {
      await recordBusinessFeedback({
        target_account_id: opportunity.target_account_id,
        hypothesis_id: opportunity.source_hypothesis_id,
        opportunity_id: opportunity.id,
        reason_id: reasonId || undefined,
        feedback_type: feedbackType,
        outcome,
        notes: notes.trim() || undefined,
        effective_at: new Date().toISOString(),
        request_key: crypto.randomUUID(),
      });
      setNotes("");
      setDetail("");
      setReasonId("");
      await reload();
    } catch (error) {
      onError(error instanceof Error ? error.message : "业务反馈记录失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="business-feedback" className="space-y-5">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">BUSINESS FEEDBACK</p>
        <h2 className="mt-1 text-xl font-semibold text-neutral-950">记录真实业务结果</h2>
        <p className="mt-1 text-sm text-neutral-600">反馈进入审计账本和离线校准，不会在线自动修改 Skill、权重或历史报告。</p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm font-medium text-neutral-700">
          反馈类型
          <select value={feedbackType} onChange={(event) => setFeedbackType(event.target.value as FeedbackType)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
            {availableTypes.map((item) => <option key={item} value={item}>{FEEDBACK_LABEL[item]}</option>)}
          </select>
        </label>
        {requiredCategory && (
          <label className="text-sm font-medium text-neutral-700">
            业务原因（必填）
            <select value={reasonId} onChange={(event) => setReasonId(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
              <option value="">请选择</option>
              {eligibleReasons.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </label>
        )}
        <label className="text-sm font-medium text-neutral-700 md:col-span-2">
          核验结果
          <textarea value={detail} onChange={(event) => setDetail(event.target.value)} rows={3} maxLength={2000} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" placeholder="说明客户反馈、阶段事实或识别错误" />
        </label>
        <label className="text-sm font-medium text-neutral-700 md:col-span-2">
          内部备注（可选）
          <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} maxLength={4000} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
        </label>
      </div>

      {requiredCategory && eligibleReasons.length === 0 && (
        <div className="grid gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 md:grid-cols-[1fr_1fr_auto]">
          <input value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} placeholder="原因代码，如 CUSTOMER_DELAY" className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm" />
          <input value={reasonLabel} onChange={(event) => setReasonLabel(event.target.value)} placeholder="原因名称" className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm" />
          <Button size="sm" variant="secondary" isLoading={busy} onClick={() => void createReason()}>新增原因</Button>
        </div>
      )}

      <Button isLoading={busy} onClick={() => void submit()}>记录业务反馈</Button>

      <div>
        <h3 className="text-base font-semibold text-neutral-950">反馈历史</h3>
        <div className="mt-3 space-y-2">
          {records.length === 0 ? <p className="rounded-lg border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-500">尚无业务反馈。</p> : records.map((item) => (
            <article key={item.id} className="rounded-lg border border-neutral-200 p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium text-neutral-950">{FEEDBACK_LABEL[item.feedback_type]}</p>
                <StatusBadge status={item.feedback_type} label={new Date(item.effective_at).toLocaleDateString("zh-CN")} />
              </div>
              {typeof item.outcome_data.detail === "string" && <p className="mt-2 text-sm text-neutral-700">{item.outcome_data.detail}</p>}
              {item.notes && <p className="mt-1 text-xs text-neutral-500">备注：{item.notes}</p>}
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
