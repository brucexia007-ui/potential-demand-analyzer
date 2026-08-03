"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

type ModelConfig = {
  default_model: string;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
};

type AvailableModels = {
  models: string[];
  default: string;
};

export default function ModelSettingsPage() {
  const router = useRouter();
  const [config, setConfig] = useState<ModelConfig>({
    default_model: "qwen3.5-plus",
    temperature: 0.2,
    timeout_seconds: 60,
    max_retries: 2,
  });
  const [available, setAvailable] = useState<AvailableModels>({
    models: [],
    default: "",
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const { error: toastError, success: toastSuccess } = useToast();

  useEffect(() => {
    const load = async () => {
      setIsLoading(true);
      try {
        const [confRes, availRes] = await Promise.all([
          authenticatedFetch("/api/models"),
          authenticatedFetch("/api/models/available"),
        ]);
        if (confRes.ok) setConfig(await confRes.json());
        if (availRes.ok) setAvailable(await availRes.json());
      } catch (err) {
        toastError("加载设置失败，请检查后端服务");
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, []);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const res = await authenticatedFetch("/api/models", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error("保存失败");
      toastSuccess("配置已保存");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <main className="min-h-screen pb-12">
        <div className="mx-auto flex max-w-4xl justify-center px-4 py-12">
          <div className="mr-3 h-6 w-6 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
          <span className="text-neutral-600">加载配置...</span>
        </div>
      </main>
    );
  }

  return (
    <PageShell>
      <PageHeader
        eyebrow="MODEL DEFAULTS"
        title="模型配置"
        description="配置 LLM 默认模型和调用参数"
        action={
          <Button variant="secondary" onClick={() => router.push("/")}>
            返回首页
          </Button>
        }
      />

        <Card variant="bordered" padding="lg">
          {/* 模型选择 */}
          <div className="mb-8">
            <label className="block text-sm font-medium text-neutral-700 mb-3">
              默认模型
            </label>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {available.models.map((model) => (
                <button
                  key={model}
                  onClick={() => setConfig({ ...config, default_model: model })}
                  className={`rounded-lg border px-4 py-3 text-left text-sm font-medium transition-all ${
                    config.default_model === model
                      ? "border-neutral-950 bg-neutral-950 text-white"
                      : "border-neutral-950/10 bg-white/80 text-neutral-600 hover:border-neutral-950/30"
                  }`}
                >
                  {model}
                </button>
              ))}
            </div>
          </div>

          {/* Temperature */}
          <div className="mb-8">
            <label className="block text-sm font-medium text-neutral-700 mb-2">
              温度 ({(config.temperature * 100).toFixed(0)}%)
            </label>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={config.temperature}
              onChange={(e) =>
                setConfig({ ...config, temperature: parseFloat(e.target.value) })
              }
              className="w-full accent-neutral-950"
            />
            <div className="flex justify-between text-xs text-neutral-500 mt-1">
              <span>0 (精确)</span>
              <span>1 (创造)</span>
            </div>
          </div>

          {/* 超时 */}
          <div className="mb-8">
            <label className="block text-sm font-medium text-neutral-700 mb-2">
              超时时间 ({config.timeout_seconds} 秒)
            </label>
            <input
              type="range"
              min="5"
              max="300"
              step="5"
              value={config.timeout_seconds}
              onChange={(e) =>
                setConfig({ ...config, timeout_seconds: parseInt(e.target.value) })
              }
              className="w-full accent-neutral-950"
            />
            <div className="flex justify-between text-xs text-neutral-500 mt-1">
              <span>5s</span>
              <span>300s</span>
            </div>
          </div>

          {/* 最大重试 */}
          <div className="mb-8">
            <label className="block text-sm font-medium text-neutral-700 mb-2">
              最大重试次数 ({config.max_retries})
            </label>
            <input
              type="range"
              min="0"
              max="10"
              step="1"
              value={config.max_retries}
              onChange={(e) =>
                setConfig({ ...config, max_retries: parseInt(e.target.value) })
              }
              className="w-full accent-neutral-950"
            />
            <div className="flex justify-between text-xs text-neutral-500 mt-1">
              <span>0</span>
              <span>10</span>
            </div>
          </div>

          <Button
            variant="primary"
            size="lg"
            onClick={handleSave}
            isLoading={isSaving}
            className="w-full"
          >
            {isSaving ? "保存中..." : "保存配置"}
          </Button>
        </Card>
    </PageShell>
  );
}
