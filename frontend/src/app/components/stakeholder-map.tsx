"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import {
  archiveOpportunityStakeholder,
  createOpportunityStakeholder,
  updateOpportunityStakeholder,
  type OpportunityStakeholder,
  type OpportunityStakeholderPayload,
} from "@/lib/opportunities";
import type { WorkbenchClaim, WorkbenchOpportunity } from "@/lib/target-accounts";


type Props = {
  accountId: string;
  stakeholders: OpportunityStakeholder[];
  opportunities: WorkbenchOpportunity[];
  claims: WorkbenchClaim[];
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
};

const ROLE_LABEL: Record<string, string> = {
  ECONOMIC_BUYER: "经济决策人",
  BUSINESS_OWNER: "业务负责人",
  TECHNICAL_DECISION_MAKER: "技术决策人",
  SECURITY_COMPLIANCE: "安全/合规负责人",
  PROCUREMENT: "采购/招标",
  USER: "使用部门",
  CHAMPION: "内部支持者",
  BLOCKER: "反对者/阻断者",
  OTHER: "其他角色",
};

const EMPTY: OpportunityStakeholderPayload = {
  role_type: "BUSINESS_OWNER",
  truth_status: "SALES_JUDGMENT",
  influence: "UNKNOWN",
  attitude: "UNKNOWN",
  relationship_strength: "UNKNOWN",
};

export function StakeholderMap({ accountId, stakeholders, opportunities, claims, onChanged, onError }: Props) {
  const [editing, setEditing] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<OpportunityStakeholderPayload>(EMPTY);
  const [submitting, setSubmitting] = useState(false);

  const eligibleClaims = claims.filter((claim) => (
    draft.truth_status === "CUSTOMER_CONFIRMED"
      ? claim.status === "CUSTOMER_CONFIRMED"
      : claim.status === "SUPPORTED" || claim.status === "CUSTOMER_CONFIRMED"
  ));

  const startCreate = () => {
    setEditingId(null);
    setDraft(EMPTY);
    setEditing(true);
  };

  const startEdit = (item: OpportunityStakeholder) => {
    setEditingId(item.id);
    setDraft({
      role_type: item.role_type,
      truth_status: item.truth_status,
      opportunity_id: item.opportunity_id ?? undefined,
      full_name: item.full_name ?? undefined,
      role_title: item.role_title ?? undefined,
      department: item.department ?? undefined,
      influence: item.influence,
      attitude: item.attitude,
      goals: item.goals,
      concerns: item.concerns,
      relationship_strength: item.relationship_strength,
      source_claim_id: item.source_claim_id ?? undefined,
      communication_strategy: item.communication_strategy,
    });
    setEditing(true);
  };

  const submit = async () => {
    if (draft.truth_status !== "SALES_JUDGMENT" && !draft.source_claim_id) {
      onError("公开推断或客户确认必须选择证据 Claim");
      return;
    }
    setSubmitting(true);
    try {
      if (editingId) {
        await updateOpportunityStakeholder(editingId, draft);
      } else {
        await createOpportunityStakeholder(accountId, draft);
      }
      setEditing(false);
      setEditingId(null);
      setDraft(EMPTY);
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "利益相关者保存失败");
    } finally {
      setSubmitting(false);
    }
  };

  const archive = async (id: string) => {
    try {
      await archiveOpportunityStakeholder(id);
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "利益相关者归档失败");
    }
  };

  return (
    <section>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-neutral-950">客户决策链</h2>
          <p className="mt-1 text-sm text-neutral-500">持续维护影响力、态度和关系；推断、销售判断、客户确认必须明确区分。</p>
        </div>
        <Button type="button" size="sm" variant="secondary" onClick={startCreate}>新增角色</Button>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {stakeholders.length === 0 ? (
          <p className="rounded-lg border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-500 md:col-span-2">尚未建立客户决策链。</p>
        ) : stakeholders.map((item) => (
          <article key={item.id} className="rounded-lg border border-neutral-200 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-semibold text-neutral-950">{item.full_name || ROLE_LABEL[item.role_type] || item.role_type}</p>
                <p className="mt-1 text-xs text-neutral-500">{item.full_name ? `${ROLE_LABEL[item.role_type] || item.role_type} · ` : ""}{item.department || "部门未知"}{item.role_title ? ` · ${item.role_title}` : ""}</p>
              </div>
              <StatusBadge status={item.truth_status} label={item.truth_status} />
            </div>
            <p className="mt-3 text-sm text-neutral-700">影响力 {item.influence} · 态度 {item.attitude} · 关系 {item.relationship_strength}</p>
            {item.goals && <p className="mt-2 text-sm text-neutral-700">目标：{item.goals}</p>}
            {item.concerns && <p className="mt-1 text-sm text-neutral-700">顾虑：{item.concerns}</p>}
            {item.communication_strategy && <p className="mt-1 text-sm text-neutral-700">沟通策略：{item.communication_strategy}</p>}
            <div className="mt-3 flex gap-3 text-sm">
              <button type="button" onClick={() => startEdit(item)} className="font-medium text-neutral-800 underline decoration-neutral-300 underline-offset-4">编辑</button>
              <button type="button" onClick={() => void archive(item.id)} className="font-medium text-red-700 underline decoration-red-200 underline-offset-4">归档</button>
            </div>
          </article>
        ))}
      </div>

      {editing && (
        <div className="mt-4 grid gap-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4 md:grid-cols-2">
          <label className="text-sm font-medium text-neutral-700">
            角色
            <select value={draft.role_type} onChange={(event) => setDraft({ ...draft, role_type: event.target.value })} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
              {Object.entries(ROLE_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            信息真实性
            <select value={draft.truth_status} onChange={(event) => setDraft({ ...draft, truth_status: event.target.value as OpportunityStakeholderPayload["truth_status"], source_claim_id: undefined })} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
              <option value="SALES_JUDGMENT">销售判断</option>
              <option value="PUBLIC_INFERENCE">公开信息推断</option>
              <option value="CUSTOMER_CONFIRMED">客户已确认</option>
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            姓名（未知可留空）
            <input value={draft.full_name ?? ""} onChange={(event) => setDraft({ ...draft, full_name: event.target.value })} maxLength={255} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
          </label>
          <label className="text-sm font-medium text-neutral-700">
            部门/职位
            <div className="mt-1 grid grid-cols-2 gap-2">
              <input value={draft.department ?? ""} onChange={(event) => setDraft({ ...draft, department: event.target.value })} placeholder="部门" maxLength={255} className="rounded-lg border border-neutral-300 bg-white px-3 py-2" />
              <input value={draft.role_title ?? ""} onChange={(event) => setDraft({ ...draft, role_title: event.target.value })} placeholder="职位" maxLength={255} className="rounded-lg border border-neutral-300 bg-white px-3 py-2" />
            </div>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            关联正式商机（可选）
            <select value={draft.opportunity_id ?? ""} onChange={(event) => setDraft({ ...draft, opportunity_id: event.target.value || undefined })} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
              <option value="">客户级角色</option>
              {opportunities.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            证据 Claim
            <select value={draft.source_claim_id ?? ""} disabled={draft.truth_status === "SALES_JUDGMENT"} onChange={(event) => setDraft({ ...draft, source_claim_id: event.target.value || undefined })} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 disabled:bg-neutral-100">
              <option value="">请选择</option>
              {eligibleClaims.map((claim) => <option key={claim.id} value={claim.id}>{claim.status} · {claim.claim_text}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            影响力
            <select value={draft.influence} onChange={(event) => setDraft({ ...draft, influence: event.target.value as OpportunityStakeholderPayload["influence"] })} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
              {(["UNKNOWN", "LOW", "MEDIUM", "HIGH"] as const).map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            态度 / 关系
            <div className="mt-1 grid grid-cols-2 gap-2">
              <select value={draft.attitude} onChange={(event) => setDraft({ ...draft, attitude: event.target.value as OpportunityStakeholderPayload["attitude"] })} className="rounded-lg border border-neutral-300 bg-white px-3 py-2">
                {(["UNKNOWN", "SUPPORTIVE", "NEUTRAL", "OPPOSED"] as const).map((item) => <option key={item}>{item}</option>)}
              </select>
              <select value={draft.relationship_strength} onChange={(event) => setDraft({ ...draft, relationship_strength: event.target.value as OpportunityStakeholderPayload["relationship_strength"] })} className="rounded-lg border border-neutral-300 bg-white px-3 py-2">
                {(["UNKNOWN", "NONE", "WEAK", "MEDIUM", "STRONG"] as const).map((item) => <option key={item}>{item}</option>)}
              </select>
            </div>
          </label>
          {(["goals", "concerns", "communication_strategy"] as const).map((field) => (
            <label key={field} className="text-sm font-medium text-neutral-700">
              {{ goals: "关注目标", concerns: "主要顾虑", communication_strategy: "建议沟通策略" }[field]}
              <textarea value={draft[field] ?? ""} onChange={(event) => setDraft({ ...draft, [field]: event.target.value })} rows={2} maxLength={4000} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
            </label>
          ))}
          <div className="flex gap-2 md:col-span-2">
            <Button type="button" size="sm" isLoading={submitting} onClick={() => void submit()}>保存角色</Button>
            <Button type="button" size="sm" variant="ghost" disabled={submitting} onClick={() => setEditing(false)}>取消</Button>
          </div>
        </div>
      )}
    </section>
  );
}
