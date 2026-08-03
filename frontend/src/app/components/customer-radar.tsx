"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/workspace";
import { listCapabilityProfiles, type CapabilityProfile } from "@/lib/capabilities";
import {
  createWatchSubscription,
  listWatchCheckRuns,
  listWatchSubscriptions,
  pauseWatchSubscription,
  resumeWatchSubscription,
  updateWatchSubscription,
  type WatchCheckRun,
  type WatchFrequency,
  type WatchSubscription,
  type WatchTopic,
} from "@/lib/watchlist";


type Props = {
  accountId: string;
  accountStatus: string;
  onError: (message: string) => void;
};

const TOPICS: Array<{ value: WatchTopic; label: string; detail: string }> = [
  { value: "COMPANY_PROFILE", label: "企业动态", detail: "主体、业务与组织变化" },
  { value: "PROCUREMENT", label: "采购事件", detail: "采购、招标与建设阶段" },
  { value: "POLICY", label: "政策状态", detail: "政策发布、生效与失效" },
  { value: "CONTRACT_WINDOW", label: "合同窗口", detail: "现有合同、续约与替换窗口" },
  { value: "LEADERSHIP", label: "关键人员", detail: "管理层与决策角色变化" },
  { value: "PRODUCT_FIT", label: "产品适配", detail: "需求与我方能力变化" },
];

const FREQUENCY_LABEL: Record<WatchFrequency, string> = {
  DAILY: "每日",
  WEEKLY: "每周",
  MONTHLY: "每月",
};

const CATEGORY_LABEL: Record<string, string> = {
  procurement: "采购事件",
  policy: "政策状态",
  contract_window: "合同窗口",
  claim: "关键结论",
};

const formatTime = (value: string | null) => value
  ? new Date(value).toLocaleString("zh-CN", { hour12: false })
  : "未安排";

export function CustomerRadar({ accountId, accountStatus, onError }: Props) {
  const [subscription, setSubscription] = useState<WatchSubscription | null>(null);
  const [runs, setRuns] = useState<WatchCheckRun[]>([]);
  const [profiles, setProfiles] = useState<CapabilityProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [topics, setTopics] = useState<WatchTopic[]>(["PROCUREMENT", "POLICY", "CONTRACT_WINDOW"]);
  const [frequency, setFrequency] = useState<WatchFrequency>("WEEKLY");
  const [externalBudget, setExternalBudget] = useState(20);
  const [tokenBudget, setTokenBudget] = useState(120000);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    const items = await listWatchSubscriptions(accountId);
    const current = items[0] ?? null;
    setSubscription(current);
    if (current) {
      setTopics(current.topics);
      setFrequency(current.frequency);
      setExternalBudget(current.max_external_calls);
      setTokenBudget(current.max_input_tokens);
      setRuns(await listWatchCheckRuns(current.id));
    } else {
      setRuns([]);
    }
  }, [accountId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([reload(), listCapabilityProfiles(false)])
      .then(([, capabilityProfiles]) => {
        if (!active) return;
        const available = capabilityProfiles.filter((item) => item.status === "ACTIVE");
        setProfiles(available);
        setProfileId(available.find((item) => item.is_default)?.id ?? available[0]?.id ?? "");
      })
      .catch((error) => {
        if (active) onError(error instanceof Error ? error.message : "客户雷达加载失败");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [onError, reload]);

  const toggleTopic = (topic: WatchTopic) => {
    setTopics((current) => current.includes(topic)
      ? current.filter((item) => item !== topic)
      : [...current, topic]);
  };

  const create = async () => {
    if (topics.length === 0) {
      onError("请至少选择一个雷达主题");
      return;
    }
    setBusy(true);
    try {
      const created = await createWatchSubscription({
        target_account_id: accountId,
        capability_profile_id: profileId || undefined,
        root_skill_name: "pilot-opportunity",
        topics,
        frequency,
        timezone_name: "Asia/Shanghai",
        max_external_calls: externalBudget,
        max_input_tokens: tokenBudget,
        start_immediately: true,
      });
      setSubscription(created);
      setRuns([]);
    } catch (error) {
      onError(error instanceof Error ? error.message : "客户雷达订阅失败");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!subscription || topics.length === 0) {
      onError("请至少选择一个雷达主题");
      return;
    }
    setBusy(true);
    try {
      setSubscription(await updateWatchSubscription(subscription.id, {
        topics,
        frequency,
        max_external_calls: externalBudget,
        max_input_tokens: tokenBudget,
      }));
    } catch (error) {
      onError(error instanceof Error ? error.message : "雷达设置保存失败");
    } finally {
      setBusy(false);
    }
  };

  const setPaused = async (paused: boolean) => {
    if (!subscription) return;
    setBusy(true);
    try {
      setSubscription(paused
        ? await pauseWatchSubscription(subscription.id)
        : await resumeWatchSubscription(subscription.id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "雷达状态更新失败");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <p className="py-8 text-center text-sm text-neutral-500">正在读取客户雷达…</p>;
  }

  if (accountStatus !== "CONFIRMED") {
    return (
      <section data-testid="customer-radar">
        <h2 className="text-xl font-semibold text-neutral-950">客户雷达</h2>
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          完成目标企业主体消歧并确认后，才能建立持续研究订阅。
        </p>
      </section>
    );
  }

  return (
    <section data-testid="customer-radar" className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">CUSTOMER RADAR</p>
          <h2 className="mt-1 text-xl font-semibold text-neutral-950">持续增量研究</h2>
          <p className="mt-1 text-sm text-neutral-600">只检查上次运行后的新增或状态变化内容，旧证据按内容指纹去重。</p>
        </div>
        {subscription && <StatusBadge status={subscription.status} label={subscription.status === "ACTIVE" ? "监控中" : "已暂停"} />}
      </div>

      {subscription && (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg bg-neutral-100 p-3"><p className="text-xs text-neutral-500">下次检查</p><p className="mt-1 text-sm font-medium text-neutral-950">{formatTime(subscription.next_run_at)}</p></div>
          <div className="rounded-lg bg-neutral-100 p-3"><p className="text-xs text-neutral-500">上次计划</p><p className="mt-1 text-sm font-medium text-neutral-950">{formatTime(subscription.last_run_at)}</p></div>
          <div className="rounded-lg bg-neutral-100 p-3"><p className="text-xs text-neutral-500">检查频率</p><p className="mt-1 text-sm font-medium text-neutral-950">{FREQUENCY_LABEL[subscription.frequency]}</p></div>
        </div>
      )}

      <div>
        <p className="text-sm font-medium text-neutral-800">监控主题</p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {TOPICS.map((item) => (
            <label key={item.value} className={`cursor-pointer rounded-lg border p-3 ${topics.includes(item.value) ? "border-neutral-950 bg-neutral-50" : "border-neutral-200 bg-white"}`}>
              <span className="flex items-center gap-2 text-sm font-medium text-neutral-950">
                <input type="checkbox" checked={topics.includes(item.value)} onChange={() => toggleTopic(item.value)} />
                {item.label}
              </span>
              <span className="mt-1 block pl-5 text-xs text-neutral-500">{item.detail}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <label className="text-sm font-medium text-neutral-700">
          频率
          <select value={frequency} onChange={(event) => setFrequency(event.target.value as WatchFrequency)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
            {(Object.keys(FREQUENCY_LABEL) as WatchFrequency[]).map((item) => <option key={item} value={item}>{FREQUENCY_LABEL[item]}</option>)}
          </select>
        </label>
        {!subscription && (
          <label className="text-sm font-medium text-neutral-700">
            能力档案（可选）
            <select value={profileId} onChange={(event) => setProfileId(event.target.value)} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2">
              <option value="">仅客户外部研究</option>
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}{profile.is_default ? "（默认）" : ""}</option>)}
            </select>
          </label>
        )}
        <label className="text-sm font-medium text-neutral-700">
          每轮外部调用上限
          <input type="number" min={0} max={1000} value={externalBudget} onChange={(event) => setExternalBudget(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
        </label>
        <label className="text-sm font-medium text-neutral-700">
          每轮输入 Token 上限
          <input type="number" min={0} max={1000000} value={tokenBudget} onChange={(event) => setTokenBudget(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2" />
        </label>
      </div>

      <div className="flex flex-wrap gap-2">
        {!subscription ? (
          <Button onClick={() => void create()} isLoading={busy}>建立雷达订阅</Button>
        ) : (
          <>
            <Button onClick={() => void save()} isLoading={busy}>保存主题与预算</Button>
            {subscription.status === "ACTIVE"
              ? <Button variant="secondary" disabled={busy} onClick={() => void setPaused(true)}>暂停雷达</Button>
              : <Button variant="secondary" disabled={busy} onClick={() => void setPaused(false)}>恢复雷达</Button>}
            <Button variant="ghost" disabled={busy} onClick={() => void reload()}>刷新结果</Button>
          </>
        )}
      </div>

      {subscription && (
        <div>
          <h3 className="text-base font-semibold text-neutral-950">最近检查</h3>
          <div className="mt-3 space-y-3">
            {runs.length === 0 ? (
              <p className="rounded-lg border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-500">尚无检查记录；首次到期运行后在此展示增量变化。</p>
            ) : runs.map((run) => {
              const categories = Object.entries(run.change_summary.categories ?? {})
                .filter(([, hashes]) => Array.isArray(hashes) && hashes.length > 0);
              return (
                <article key={run.id} className="rounded-lg border border-neutral-200 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-neutral-950">检查截止 {run.analysis_as_of_date}</p>
                      <p className="mt-1 text-xs text-neutral-500">计划于 {formatTime(run.scheduled_for)} · 实际调用 {run.usage.external_calls ?? 0} 次 · 输入 {(run.usage.input_tokens ?? 0).toLocaleString()} Token</p>
                    </div>
                    <StatusBadge status={run.status} label={run.status} />
                  </div>
                  {run.error_message ? (
                    <p className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-800">{run.error_code ? `${run.error_code}：` : ""}{run.error_message}</p>
                  ) : (
                    <div className="mt-3 flex flex-wrap gap-2 text-xs">
                      {categories.length === 0 && <span className="rounded-full bg-neutral-100 px-3 py-1 text-neutral-600">无实质变化</span>}
                      {categories.map(([key, hashes]) => <span key={key} className="rounded-full bg-cyan-50 px-3 py-1 text-cyan-800">{CATEGORY_LABEL[key] ?? key} {hashes?.length ?? 0}</span>)}
                      {run.change_summary.gate_level && <span className="rounded-full bg-lime-50 px-3 py-1 text-lime-800">最新 Gate {run.change_summary.gate_level}</span>}
                    </div>
                  )}
                  {run.task_id && <a href={`/tasks/${run.task_id}`} className="mt-3 inline-flex text-sm font-medium text-neutral-950 underline decoration-neutral-300 underline-offset-4">查看本轮研究</a>}
                </article>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
