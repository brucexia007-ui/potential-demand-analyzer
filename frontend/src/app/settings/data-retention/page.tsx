"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

type RetentionConfig = {
  task_records_days: number;
  report_content_days: number;
  evidence_index_days: number;
  url_and_snippet_days: number;
  raw_web_text_days: number;
  html_snapshot_days: number;
  screenshot_days: number;
  fetch_cache_days: number;
  task_logs_days: number;
  temp_files_days: number;
};

const DEFAULTS: RetentionConfig = {
  task_records_days: 0,
  report_content_days: 0,
  evidence_index_days: 0,
  url_and_snippet_days: 0,
  raw_web_text_days: 90,
  html_snapshot_days: 30,
  screenshot_days: 30,
  fetch_cache_days: 7,
  task_logs_days: 30,
  temp_files_days: 3,
};

const ITEMS: { label: string; key: keyof RetentionConfig; permanent?: boolean }[] = [
  { label: "任务记录", key: "task_records_days", permanent: true },
  { label: "报告正文", key: "report_content_days", permanent: true },
  { label: "证据索引", key: "evidence_index_days", permanent: true },
  { label: "URL 和摘要", key: "url_and_snippet_days", permanent: true },
  { label: "原始网页文本", key: "raw_web_text_days" },
  { label: "HTML 快照", key: "html_snapshot_days" },
  { label: "页面截图", key: "screenshot_days" },
  { label: "抓取缓存", key: "fetch_cache_days" },
  { label: "任务日志", key: "task_logs_days" },
  { label: "临时文件", key: "temp_files_days" },
];

function formatDays(days: number): string {
  return days === 0 ? "永久保留" : `${days} 天`;
}

function apiHeaders(): Record<string, string> {
  return { "Content-Type": "application/json" };
}

export default function DataRetentionPage() {
  const { error: toastError, success: toastSuccess } = useToast();

  const [config, setConfig] = useState<RetentionConfig>(DEFAULTS);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    authenticatedFetch("/api/config/data-retention")
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
      const res = await authenticatedFetch("/api/config/data-retention", {
        method: "PUT",
        headers: apiHeaders(),
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toastSuccess("数据保留策略已保存");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return <PageShell><PageHeader title="数据保留" /><p className="text-neutral-500 px-4">加载中...</p></PageShell>;
  }

  return (
    <PageShell>
      <PageHeader title="数据保留策略" description="配置各类数据的保留时间。0 天 = 永久保留" />

      <Card variant="bordered" padding="lg" className="max-w-2xl">
        <div className="space-y-5">
          {ITEMS.map(({ label, key, permanent }) => (
            <div key={key} className="rounded-lg border border-neutral-950/10 bg-white/75 p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-neutral-800">{label}</span>
                <span className={`text-sm font-semibold ${config[key] === 0 ? "text-green-700" : "text-neutral-600"}`}>
                  {formatDays(config[key])}
                </span>
              </div>
              {permanent && (
                <p className="text-xs text-neutral-400 mb-2">此数据默认永久保留，不可优先删除</p>
              )}
              {!permanent && (
                <input type="range" min={1} max={365}
                  value={config[key] === 0 ? 365 : config[key]}
                  onChange={(e) => setConfig({ ...config, [key]: parseInt(e.target.value) })}
                  className="w-full accent-neutral-950" />
              )}
              {!permanent && (
                <div className="mt-1 flex justify-between text-xs text-neutral-400">
                  <span>1 天</span>
                  <span>365 天</span>
                  <button type="button"
                    className={`underline ${config[key] === 0 ? "text-green-700" : "text-neutral-500"}`}
                    onClick={() => setConfig({ ...config, [key]: 0 })}>
                    设为永久
                  </button>
                </div>
              )}
            </div>
          ))}

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
