"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { listCapabilityProfiles, type CapabilityProfile } from "@/lib/capabilities";
import {
  confirmDiscoveryPlan,
  launchDiscoveryPlan,
  previewDiscoveryPlan,
  type DiscoveryDepth,
  type DiscoveryPlan,
} from "@/lib/opportunities";

type Props = {
  accountId: string;
  accountName: string;
  onError: (message: string) => void;
};

const depthLabel: Record<DiscoveryDepth, string> = {
  quick: "快速扫描",
  standard: "标准研究",
  deep: "深度研究",
};

export function OpportunityResearchPlan({ accountId, accountName, onError }: Props) {
  const router = useRouter();
  const [profiles, setProfiles] = useState<CapabilityProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [direction, setDirection] = useState("发现并验证目标企业的潜在商机线索");
  const [depth, setDepth] = useState<DiscoveryDepth>("standard");
  const [plan, setPlan] = useState<DiscoveryPlan | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    listCapabilityProfiles(false)
      .then((items) => {
        if (!active) return;
        const available = items.filter((item) => item.status === "ACTIVE");
        setProfiles(available);
        setProfileId(available.find((item) => item.is_default)?.id ?? available[0]?.id ?? "");
      })
      .catch((error) => onError(error instanceof Error ? error.message : "能力档案加载失败"));
    return () => { active = false; };
  }, [onError]);

  const resetPlan = () => setPlan(null);

  const preview = async () => {
    if (!profileId) {
      onError("请先创建并启用一个企业能力档案");
      return;
    }
    if (!direction.trim()) {
      onError("研究方向不能为空");
      return;
    }
    setBusy(true);
    try {
      setPlan(await previewDiscoveryPlan({
        target_account_id: accountId,
        capability_profile_id: profileId,
        root_skill_name: "pilot-opportunity",
        demand_direction: direction.trim(),
        depth,
      }));
    } catch (error) {
      onError(error instanceof Error ? error.message : "研究计划生成失败");
    } finally {
      setBusy(false);
    }
  };

  const confirmAndLaunch = async () => {
    if (!plan) return;
    setBusy(true);
    try {
      const executable = plan.requires_confirmation && plan.status === "PREVIEWED"
        ? await confirmDiscoveryPlan(plan.id)
        : plan;
      setPlan(executable);
      const result = await launchDiscoveryPlan(executable.id);
      router.push(`/tasks/${result.task_id}`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "自动发现任务启动失败");
      setBusy(false);
    }
  };

  if (!plan) {
    return (
      <section data-testid="opportunity-research-plan" className="space-y-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">AUTOMATIC DISCOVERY</p>
          <h2 className="mt-1 text-xl font-semibold text-neutral-950">自动发现商机线索</h2>
          <p className="mt-1 text-sm text-neutral-600">系统会基于我方产品能力研究 {accountName}，先生成可审核计划，再开始外部研究。</p>
        </div>
        <label className="block text-sm font-medium text-neutral-700">
          企业能力档案
          <select
            value={profileId}
            onChange={(event) => { setProfileId(event.target.value); resetPlan(); }}
            className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-neutral-950"
          >
            {profiles.length === 0 && <option value="">暂无可用能力档案</option>}
            {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}{profile.is_default ? "（默认）" : ""}</option>)}
          </select>
        </label>
        <label className="block text-sm font-medium text-neutral-700">
          研究方向
          <input
            value={direction}
            onChange={(event) => { setDirection(event.target.value); resetPlan(); }}
            className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-neutral-950"
          />
        </label>
        <label className="block text-sm font-medium text-neutral-700">
          研究深度
          <select
            value={depth}
            onChange={(event) => { setDepth(event.target.value as DiscoveryDepth); resetPlan(); }}
            className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-neutral-950"
          >
            {(Object.keys(depthLabel) as DiscoveryDepth[]).map((item) => <option key={item} value={item}>{depthLabel[item]}</option>)}
          </select>
        </label>
        <Button onClick={preview} isLoading={busy} disabled={!profileId}>生成研究计划</Button>
      </section>
    );
  }

  const snapshot = plan.snapshot;
  const estimate = snapshot.estimate;
  return (
    <section data-testid="opportunity-research-plan-preview" className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">PLAN PREVIEW</p>
          <h2 className="mt-1 text-xl font-semibold text-neutral-950">执行前确认研究计划</h2>
          <p className="mt-1 text-xs text-neutral-500">计划指纹 {plan.input_hash.slice(0, 12)} · {new Date(plan.expires_at).toLocaleTimeString("zh-CN")} 前有效</p>
        </div>
        <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">{depthLabel[snapshot.scope.depth]}</span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-neutral-200 p-4">
          <p className="text-xs text-neutral-500">目标企业</p>
          <p className="mt-1 font-semibold text-neutral-950">{snapshot.target.official_name || snapshot.target.input_name}</p>
          <p className="mt-1 text-xs text-neutral-500">主体状态：{snapshot.target.status}</p>
        </div>
        <div className="rounded-lg border border-neutral-200 p-4">
          <p className="text-xs text-neutral-500">我方能力范围</p>
          <p className="mt-1 font-semibold text-neutral-950">{snapshot.capability_profile.name}</p>
          <p className="mt-1 text-xs text-neutral-500">{snapshot.capability_profile.products.map((item) => `${item.name} ${item.version_label}`).join("、")}</p>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-neutral-950">待验证假设</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-neutral-700">
          {snapshot.research_hypotheses.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-neutral-950">Skill 与研究维度</h3>
        <p className="mt-1 text-xs text-neutral-500">{snapshot.skill.root_name} · {snapshot.skill.version}</p>
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {snapshot.skill.research_dimensions.map((item) => (
            <article key={item.skill_name} className="rounded-lg border border-neutral-200 p-3">
              <p className="text-sm font-medium text-neutral-950">{item.skill_name}</p>
              <p className="mt-1 text-xs leading-5 text-neutral-600">{item.description}</p>
            </article>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg bg-neutral-100 p-3 text-center"><p className="text-xs text-neutral-500">外部调用</p><strong className="mt-1 block text-neutral-950">约 {estimate.external_calls} 次</strong></div>
        <div className="rounded-lg bg-neutral-100 p-3 text-center"><p className="text-xs text-neutral-500">输入预算</p><strong className="mt-1 block text-neutral-950">约 {estimate.input_tokens.toLocaleString()} Token</strong></div>
        <div className="rounded-lg bg-neutral-100 p-3 text-center"><p className="text-xs text-neutral-500">预计耗时</p><strong className="mt-1 block text-neutral-950">{estimate.duration_minutes.minimum}–{estimate.duration_minutes.maximum} 分钟</strong></div>
      </div>
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
        <p className="font-medium">金额成本：暂不可估算</p>
        <p className="mt-1 text-xs leading-5">{estimate.monetary_cost.reason} {estimate.basis}</p>
      </div>

      {plan.requires_confirmation && (
        <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-4">
          <p className="text-sm font-semibold text-cyan-950">确认即表示同意以下执行边界</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5 text-cyan-900">
            {snapshot.confirmation.reasons.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        <Button onClick={confirmAndLaunch} isLoading={busy}>
          {plan.requires_confirmation ? "确认计划并开始研究" : "开始快速研究"}
        </Button>
        <Button variant="secondary" onClick={resetPlan} disabled={busy}>返回修改</Button>
      </div>
    </section>
  );
}
