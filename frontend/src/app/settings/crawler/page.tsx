"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

type CrawlerConfig = {
  enable_static_fetch: boolean;
  enable_playwright_fetch: boolean;
  enable_field_agent: boolean;
  max_pages_per_task: number;
  max_page_size_mb: number;
  max_redirects: number;
  request_timeout_seconds: number;
  screenshot_enabled: boolean;
  external_agent_step_limit: number;
  external_agent_time_limit_seconds: number;
};

const DEFAULTS: CrawlerConfig = {
  enable_static_fetch: true,
  enable_playwright_fetch: true,
  enable_field_agent: false,
  max_pages_per_task: 30,
  max_page_size_mb: 5,
  max_redirects: 5,
  request_timeout_seconds: 20,
  screenshot_enabled: true,
  external_agent_step_limit: 20,
  external_agent_time_limit_seconds: 120,
};

function apiHeaders(): Record<string, string> {
  return { "Content-Type": "application/json" };
}

export default function CrawlerSettingsPage() {
  const { error: toastError, success: toastSuccess } = useToast();

  const [config, setConfig] = useState<CrawlerConfig>(DEFAULTS);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    authenticatedFetch("/api/config/crawler")
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
      const res = await authenticatedFetch("/api/config/crawler", {
        method: "PUT",
        headers: apiHeaders(),
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toastSuccess("抓取配置已保存");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return <PageShell><PageHeader title="抓取配置" /><p className="text-neutral-500 px-4">加载中...</p></PageShell>;
  }

  return (
    <PageShell>
      <PageHeader title="抓取与外部 Agent 配置" description="配置网页抓取行为和体验式背调 Agent" />

      <Card variant="bordered" padding="lg" className="max-w-2xl">
        <div className="space-y-6">
          {/* 静态抓取 */}
          <label className="flex items-center justify-between">
            <div>
              <span className="font-medium text-neutral-800">启用静态抓取</span>
              <p className="text-sm text-neutral-500">使用 HTTP 请求获取网页内容</p>
            </div>
            <input type="checkbox" checked={config.enable_static_fetch}
              onChange={(e) => setConfig({ ...config, enable_static_fetch: e.target.checked })}
              className="h-4 w-4 accent-neutral-950" />
          </label>

          {/* 动态抓取 */}
          <label className="flex items-center justify-between">
            <div>
              <span className="font-medium text-neutral-800">启用动态抓取 (Playwright)</span>
              <p className="text-sm text-neutral-500">使用浏览器渲染 JS 页面</p>
            </div>
            <input type="checkbox" checked={config.enable_playwright_fetch}
              onChange={(e) => setConfig({ ...config, enable_playwright_fetch: e.target.checked })}
              className="h-4 w-4 accent-neutral-950" />
          </label>

          {/* 外部 Agent */}
          <label className="flex items-center justify-between">
            <div>
              <span className="font-medium text-neutral-800">允许体验式背调</span>
              <p className="text-sm text-neutral-500">启用 PlaywrightFieldAgent 进行网页体验观察</p>
            </div>
            <input type="checkbox" checked={config.enable_field_agent}
              onChange={(e) => setConfig({ ...config, enable_field_agent: e.target.checked })}
              className="h-4 w-4 accent-neutral-950" />
          </label>

          <hr className="border-neutral-950/10" />

          {/* 抓取限制 */}
          {[
            { label: "单任务最大抓取页数", key: "max_pages_per_task" as const, min: 1, max: 100 },
            { label: "单页最大响应体 (MB)", key: "max_page_size_mb" as const, min: 1, max: 50 },
            { label: "最大重定向次数", key: "max_redirects" as const, min: 0, max: 20 },
            { label: "请求超时 (秒)", key: "request_timeout_seconds" as const, min: 5, max: 120 },
            { label: "外部 Agent 最大步骤数", key: "external_agent_step_limit" as const, min: 1, max: 100 },
            { label: "外部 Agent 超时 (秒)", key: "external_agent_time_limit_seconds" as const, min: 10, max: 600 },
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

          {/* 截图 */}
          <label className="flex items-center justify-between">
            <div>
              <span className="font-medium text-neutral-800">动态抓取保存截图</span>
              <p className="text-sm text-neutral-500">Playwright 抓取时自动截图</p>
            </div>
            <input type="checkbox" checked={config.screenshot_enabled}
              onChange={(e) => setConfig({ ...config, screenshot_enabled: e.target.checked })}
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
