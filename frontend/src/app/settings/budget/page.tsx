"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

type BudgetConfig = {
  monthly_budget: number | null;
  per_task_budget: number | null;
  max_concurrent_tasks: number;
  llm_max_concurrency: number;
  search_max_concurrency: number;
  enable_adaptive_concurrency: boolean;
  rate_limit_backoff_seconds: number;
  circuit_breaker_threshold: number;
  circuit_breaker_recovery_seconds: number;
  allow_provider_fallback: boolean;
};

const DEFAULTS: BudgetConfig = {
  monthly_budget: null,
  per_task_budget: null,
  max_concurrent_tasks: 2,
  llm_max_concurrency: 2,
  search_max_concurrency: 3,
  enable_adaptive_concurrency: true,
  rate_limit_backoff_seconds: 60,
  circuit_breaker_threshold: 3,
  circuit_breaker_recovery_seconds: 300,
  allow_provider_fallback: true,
};

function apiHeaders(): Record<string, string> {
  return { "Content-Type": "application/json" };
}

export default function BudgetSettingsPage() {
  const { error: toastError, success: toastSuccess } = useToast();

  const [config, setConfig] = useState<BudgetConfig>(DEFAULTS);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    authenticatedFetch("/api/config/budget")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setConfig({ ...DEFAULTS, ...data });
      })
      .catch(() => toastError("加载配置失败"))
      .finally(() => setIsLoading(false));
  }, []);

  const save = async () => {
    setIsSaving(true);
    try {
      const res = await authenticatedFetch("/api/config/budget", {
        method: "PUT",
        headers: apiHeaders(),
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toastSuccess("预算配置已保存");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return <PageShell><PageHeader title="预算配置" /><p className="text-neutral-500 px-4">加载中...</p></PageShell>;
  }

  return (
    <PageShell>
      <PageHeader title="预算与限流配置" description="控制任务成本、并发和 429 熔断策略" />

      <Card variant="bordered" padding="lg" className="max-w-2xl">
        <div className="space-y-6">
          {/* 预算 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-neutral-700">月度预算上限（空=无限制）</label>
              <input type="number" min={0} step={0.01}
                value={config.monthly_budget ?? ""}
                onChange={(e) => setConfig({ ...config, monthly_budget: e.target.value ? parseFloat(e.target.value) : null })}
                placeholder="留空表示无限制"
                className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5" />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-neutral-700">单任务预算上限（空=无限制）</label>
              <input type="number" min={0} step={0.01}
                value={config.per_task_budget ?? ""}
                onChange={(e) => setConfig({ ...config, per_task_budget: e.target.value ? parseFloat(e.target.value) : null })}
                placeholder="留空表示无限制"
                className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5" />
            </div>
          </div>

          {/* 并发 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {[
              { label: "最大并发任务数", key: "max_concurrent_tasks" as const },
              { label: "LLM 最大并发", key: "llm_max_concurrency" as const },
              { label: "搜索最大并发", key: "search_max_concurrency" as const },
            ].map(({ label, key }) => (
              <div key={key}>
                <label className="mb-1.5 block text-sm font-medium text-neutral-700">
                  {label}: <span className="font-semibold text-neutral-950">{config[key]}</span>
                </label>
                <input type="range" min={1} max={10} value={config[key]}
                  onChange={(e) => setConfig({ ...config, [key]: parseInt(e.target.value) })}
                  className="w-full accent-neutral-950" />
              </div>
            ))}
          </div>

          <hr className="border-neutral-950/10" />

          {/* 429 配置 */}
          <label className="flex items-center justify-between">
            <div>
              <span className="font-medium text-neutral-800">自适应并发</span>
              <p className="text-sm text-neutral-500">检测 429 后自动降低并发</p>
            </div>
            <input type="checkbox" checked={config.enable_adaptive_concurrency}
              onChange={(e) => setConfig({ ...config, enable_adaptive_concurrency: e.target.checked })}
              className="h-4 w-4 accent-neutral-950" />
          </label>

          {[
            { label: "429 退避秒数", key: "rate_limit_backoff_seconds" as const, min: 10, max: 600 },
            { label: "熔断阈值（连续失败次数）", key: "circuit_breaker_threshold" as const, min: 1, max: 20 },
            { label: "熔断恢复时间 (秒)", key: "circuit_breaker_recovery_seconds" as const, min: 30, max: 3600 },
          ].map(({ label, key, min, max }) => (
            <div key={key}>
              <label className="mb-1.5 block text-sm font-medium text-neutral-700">
                {label}: <span className="font-semibold text-neutral-950">{config[key]}</span>
              </label>
              <input type="range" min={min} max={max} value={config[key]}
                onChange={(e) => setConfig({ ...config, [key]: parseInt(e.target.value) })}
                className="w-full accent-neutral-950" />
            </div>
          ))}

          <label className="flex items-center justify-between">
            <div>
              <span className="font-medium text-neutral-800">Provider 自动降级</span>
              <p className="text-sm text-neutral-500">主 Provider 不可用时切换备用</p>
            </div>
            <input type="checkbox" checked={config.allow_provider_fallback}
              onChange={(e) => setConfig({ ...config, allow_provider_fallback: e.target.checked })}
              className="h-4 w-4 accent-neutral-950" />
          </label>

          <div className="flex gap-3 pt-4">
            <Button variant="primary" size="lg" onClick={save} isLoading={isSaving}>
              {isSaving ? "保存中..." : "保存配置"}
            </Button>
          </div>
        </div>
      </Card>
    </PageShell>
  );
}
