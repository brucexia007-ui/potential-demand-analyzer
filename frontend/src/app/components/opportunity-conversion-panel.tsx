"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { convertHypothesis } from "@/lib/opportunities";
import type { WorkbenchHypothesis } from "@/lib/target-accounts";


type Props = {
  hypothesis: WorkbenchHypothesis;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
};

export function OpportunityConversionPanel({ hypothesis, onChanged, onError }: Props) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(hypothesis.title);
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("CNY");
  const [amountSource, setAmountSource] = useState<"CUSTOMER_CONFIRMED" | "USER_ESTIMATE">("USER_ESTIMATE");
  const [probability, setProbability] = useState("0.2");
  const [expectedCloseDate, setExpectedCloseDate] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (hypothesis.status !== "CUSTOMER_VALIDATED") return null;
  if (hypothesis.latest_qualification?.gate_result !== "PASS") {
    return <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">客户虽已确认，但最新资格卡尚未通过，不能创建正式商机。</p>;
  }

  const submit = async () => {
    if (!title.trim() || !reason.trim()) {
      onError("请填写正式商机标题和创建依据");
      return;
    }
    const probabilityValue = Number(probability);
    if (!Number.isFinite(probabilityValue) || probabilityValue < 0 || probabilityValue > 1) {
      onError("成交概率必须在 0 到 1 之间");
      return;
    }
    if (amount && (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) < 0)) {
      onError("金额必须是非负数，且最多保留两位小数");
      return;
    }
    setSubmitting(true);
    try {
      await convertHypothesis(hypothesis.id, {
        title: title.trim(),
        reason: reason.trim(),
        request_key: crypto.randomUUID(),
        probability: probabilityValue,
        ...(amount ? { amount, currency, amount_source: amountSource } : {}),
        ...(expectedCloseDate ? { expected_close_date: expectedCloseDate } : {}),
      });
      setEditing(false);
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "正式商机创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="mt-4 border-t border-neutral-200 pt-4">
      {!editing ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-lime-50 p-3">
          <p className="text-sm text-lime-900">客户确认、G5 与资格卡通过后，可由销售人工创建正式商机。</p>
          <Button type="button" size="sm" onClick={() => setEditing(true)}>创建正式商机</Button>
        </div>
      ) : (
        <div className="grid gap-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4 md:grid-cols-2">
          <label className="text-sm font-medium text-neutral-700">
            商机标题
            <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={500} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
          </label>
          <label className="text-sm font-medium text-neutral-700">
            成交概率（0-1）
            <input type="number" min="0" max="1" step="0.05" value={probability} onChange={(event) => setProbability(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
          </label>
          <label className="text-sm font-medium text-neutral-700 md:col-span-2">
            创建依据
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} maxLength={1000} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
          </label>
          <label className="text-sm font-medium text-neutral-700">
            预计金额（可选）
            <input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="例如 1250000.00" className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
          </label>
          <label className="text-sm font-medium text-neutral-700">
            币种
            <input value={currency} onChange={(event) => setCurrency(event.target.value.toUpperCase())} maxLength={3} disabled={!amount} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 disabled:bg-neutral-100" />
          </label>
          <label className="text-sm font-medium text-neutral-700">
            金额来源
            <select value={amountSource} onChange={(event) => setAmountSource(event.target.value as typeof amountSource)} disabled={!amount} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 disabled:bg-neutral-100">
              <option value="USER_ESTIMATE">销售估算</option>
              <option value="CUSTOMER_CONFIRMED">客户确认</option>
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            预计成交日期（可选）
            <input type="date" value={expectedCloseDate} onChange={(event) => setExpectedCloseDate(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
          </label>
          <div className="flex gap-2 md:col-span-2">
            <Button type="button" size="sm" isLoading={submitting} onClick={() => void submit()}>确认创建</Button>
            <Button type="button" size="sm" variant="ghost" disabled={submitting} onClick={() => setEditing(false)}>取消</Button>
          </div>
        </div>
      )}
    </section>
  );
}
