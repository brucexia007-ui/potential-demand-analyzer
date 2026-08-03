"use client";

import { useEffect, useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";
import {
  getLlmProviderPreset,
  inferLlmProviderPreset,
  LLM_PROVIDER_PRESETS,
  type LlmProviderPresetKey,
} from "@/lib/llm-provider-presets";

type Provider = {
  id: number;
  name: string;
  provider_type: string;
  base_url: string | null;
  masked_api_key: string | null;
  models: string[];
  default_model: string | null;
  fallback_models: string[];
  enabled: boolean;
  priority: number;
  timeout_seconds: number;
  retry_count: number;
  created_at: string | null;
  updated_at: string | null;
};

type TestResult = {
  success: boolean;
  models?: string[];
  latency_ms?: number;
  error?: string;
} | null;

const EMPTY_FORM = {
  name: "",
  provider_type: "openai_compatible",
  base_url: "",
  api_key: "",
  models: "",
  default_model: "",
  enabled: true,
  priority: 100,
  timeout_seconds: 60,
  retry_count: 2,
};

export default function ProvidersPage() {
  const router = useRouter();
  const { error: toastError, success: toastSuccess } = useToast();

  const [providers, setProviders] = useState<Provider[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [selectedPreset, setSelectedPreset] =
    useState<LlmProviderPresetKey>("custom");
  const [form, setForm] = useState(EMPTY_FORM);
  const [isSaving, setIsSaving] = useState(false);
  const [testResults, setTestResults] = useState<Record<number, TestResult>>({});
  const [testingIds, setTestingIds] = useState<Set<number>>(new Set());
  const [healthMap, setHealthMap] = useState<Record<number, { status: string; consecutive_429: number }>>({});

  const apiHeaders = () => {
    return { "Content-Type": "application/json" };
  };

  const loadProviders = async () => {
    setIsLoading(true);
    try {
      const res = await authenticatedFetch("/api/config/providers");
      if (!res.ok) throw new Error("加载失败");
      setProviders(await res.json());
    } catch (err) {
      toastError("加载 Provider 列表失败");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadProviders();
    loadHealth();
  }, []);

  const loadHealth = async () => {
    try {
      const res = await authenticatedFetch("/api/config/health");
      if (res.ok) {
        const data = await res.json();
        const map: Record<number, { status: string; consecutive_429: number }> = {};
        for (const h of data.llm || []) {
          map[h.provider_id] = { status: h.status, consecutive_429: h.consecutive_429 };
        }
        setHealthMap(map);
      }
    } catch { /* fail silently */ }
  };

  const healthLabel = (status: string) => {
    const labels: Record<string, { color: string; text: string }> = {
      healthy: { color: "bg-green-100 text-green-700", text: "正常" },
      degraded: { color: "bg-yellow-100 text-yellow-700", text: "降级" },
      open: { color: "bg-red-100 text-red-700", text: "熔断" },
      half_open: { color: "border border-cyan-200 bg-cyan-50 text-cyan-700", text: "半开" },
    };
    return labels[status] || { color: "bg-neutral-100 text-neutral-500", text: status };
  };

  const openCreate = () => {
    setEditingId(null);
    setSelectedPreset("custom");
    setForm(EMPTY_FORM);
    setShowForm(true);
  };

  const openEdit = (p: Provider) => {
    setEditingId(p.id);
    setSelectedPreset(
      inferLlmProviderPreset(p.provider_type, p.base_url, p.models),
    );
    setForm({
      name: p.name,
      provider_type: p.provider_type,
      base_url: p.base_url || "",
      api_key: "", // 编辑时不回填 API Key
      models: p.models.join(", "),
      default_model: p.default_model || "",
      enabled: p.enabled,
      priority: p.priority,
      timeout_seconds: p.timeout_seconds,
      retry_count: p.retry_count,
    });
    setShowForm(true);
  };

  const applyProviderPreset = (key: LlmProviderPresetKey) => {
    const preset = getLlmProviderPreset(key);
    setSelectedPreset(key);
    setForm((current) => ({
      ...current,
      ...preset.values,
    }));
  };

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) {
      toastError("请填写 Provider 名称");
      return;
    }

    setIsSaving(true);
    try {
      const body: Record<string, unknown> = {
        name: form.name.trim(),
        provider_type: form.provider_type,
        base_url: form.base_url.trim() || null,
        api_key: form.api_key.trim() || null,
        models: form.models
          .split(",")
          .map((m) => m.trim())
          .filter(Boolean),
        default_model: form.default_model.trim() || null,
        enabled: form.enabled,
        priority: form.priority,
        timeout_seconds: form.timeout_seconds,
        retry_count: form.retry_count,
      };

      // 编辑时不传 api_key 如果为空（保留旧值）
      if (editingId && !form.api_key.trim()) {
        delete body.api_key;
      }

      const url = editingId
        ? `/api/config/providers/${editingId}`
        : "/api/config/providers";
      const method = editingId ? "PUT" : "POST";

      const res = await authenticatedFetch(url, {
        method,
        headers: apiHeaders(),
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const detail = await res.json().then((d) => d.detail).catch(() => null);
        throw new Error(detail || `保存失败 (${res.status})`);
      }

      toastSuccess(editingId ? "Provider 已更新" : "Provider 已创建");
      setShowForm(false);
      setEditingId(null);
      await loadProviders();
    } catch (err) {
      toastError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("确定要删除此 Provider 吗？")) return;
    try {
      const res = await authenticatedFetch(`/api/config/providers/${id}`, {
        method: "DELETE",
        headers: apiHeaders(),
      });
      if (!res.ok) throw new Error("删除失败");
      toastSuccess("Provider 已删除");
      await loadProviders();
    } catch (err) {
      toastError(err instanceof Error ? err.message : "删除失败");
    }
  };

  const handleTest = async (id: number) => {
    setTestingIds((prev) => new Set(prev).add(id));
    setTestResults((prev) => ({ ...prev, [id]: null }));
    try {
      const res = await authenticatedFetch(`/api/config/providers/${id}/test`, {
        method: "POST",
        headers: apiHeaders(),
      });
      const result = await res.json();
      setTestResults((prev) => ({ ...prev, [id]: result }));
    } catch {
      setTestResults((prev) => ({
        ...prev,
        [id]: { success: false, error: "请求失败" },
      }));
    } finally {
      setTestingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  if (isLoading) {
    return (
      <main className="min-h-screen pb-12">
        <div className="mx-auto flex max-w-4xl justify-center px-4 py-12">
          <div className="mr-3 h-6 w-6 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
          <span className="text-neutral-600">加载 Provider 列表...</span>
        </div>
      </main>
    );
  }

  return (
    <PageShell>
      <PageHeader
        eyebrow="MODEL ROUTING"
        title="LLM Provider 管理"
        description="配置大语言模型 API 提供商，支持多 Provider 自动降级"
        action={
          <>
            <Button variant="secondary" onClick={() => router.push("/settings/search")}>
              搜索设置
            </Button>
            <Button variant="secondary" onClick={() => router.push("/")}>
              返回首页
            </Button>
          </>
        }
      />

        {/* Provider 列表 */}
        {providers.length === 0 && !showForm ? (
          <Card variant="bordered" padding="lg">
            <div className="text-center py-8">
              <p className="text-neutral-500 mb-4">尚未配置任何 LLM Provider</p>
              <Button variant="primary" onClick={openCreate}>
                添加第一个 Provider
              </Button>
            </div>
          </Card>
        ) : (
          <div className="space-y-4 mb-8">
            {providers.map((p) => (
              <Card key={p.id} variant="bordered" padding="lg">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-2">
                      <h3 className="font-medium text-neutral-900">{p.name}</h3>
                      {/* 健康状态 */}
                      {healthMap[p.id] && (
                        <span
                          className={`inline-flex px-2 py-0.5 text-xs rounded-full ${healthLabel(healthMap[p.id].status).color}`}
                          title={`连续429: ${healthMap[p.id].consecutive_429}`}
                        >
                          {healthLabel(healthMap[p.id].status).text}
                        </span>
                      )}
                      <span
                        className={`inline-flex px-2 py-0.5 text-xs rounded-full ${
                          p.enabled
                            ? "bg-green-100 text-green-700"
                            : "bg-neutral-100 text-neutral-500"
                        }`}
                      >
                        {p.enabled ? "启用" : "禁用"}
                      </span>
                      <span className="text-xs text-neutral-400">
                        优先级: {p.priority}
                      </span>
                    </div>
                    <div className="text-sm text-neutral-600 space-y-1">
                      <p>类型: {p.provider_type} | Base URL: {p.base_url || "—"}</p>
                      <p>
                        API Key: {p.masked_api_key || "—"} | 默认模型:{" "}
                        {p.default_model || "—"}
                      </p>
                      <p>模型: {(p.models || []).join(", ") || "—"}</p>
                      <p>
                        超时: {p.timeout_seconds}s | 重试: {p.retry_count}次
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-4">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleTest(p.id)}
                      disabled={testingIds.has(p.id)}
                    >
                      {testingIds.has(p.id) ? "测试中..." : "测试连接"}
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => openEdit(p)}>
                      编辑
                    </Button>
                    <Button variant="danger" size="sm" onClick={() => handleDelete(p.id)}>
                      删除
                    </Button>
                  </div>
                </div>
                {/* 测试结果 */}
                {testResults[p.id] && (
                  <div
                    className={`mt-3 rounded-lg p-3 text-sm ${
                      testResults[p.id]!.success
                        ? "bg-green-50 border border-green-200"
                        : "bg-red-50 border border-red-200"
                    }`}
                  >
                    {testResults[p.id]!.success ? (
                      <div>
                        <p className="text-green-700 font-medium">连接成功</p>
                        <p className="text-green-600 mt-1">
                          可用模型: {(testResults[p.id]!.models || []).join(", ")}
                        </p>
                        <p className="text-green-500 text-xs mt-1">
                          耗时: {testResults[p.id]!.latency_ms}ms
                        </p>
                      </div>
                    ) : (
                      <div>
                        <p className="text-red-700 font-medium">连接失败</p>
                        <p className="text-red-600 mt-1">{testResults[p.id]!.error}</p>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}

        {/* 新建按钮（列表非空时显示） */}
        {providers.length > 0 && !showForm && (
          <Button variant="primary" onClick={openCreate}>
            添加 Provider
          </Button>
        )}

        {/* 创建/编辑表单 */}
        {showForm && (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-6">
              {editingId ? "编辑 Provider" : "新建 Provider"}
            </h2>
            <form onSubmit={handleSave}>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    接口预设
                  </label>
                  <select
                    aria-label="接口预设"
                    value={selectedPreset}
                    onChange={(e) =>
                      applyProviderPreset(e.target.value as LlmProviderPresetKey)
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  >
                    {LLM_PROVIDER_PRESETS.map((preset) => (
                      <option key={preset.key} value={preset.key}>
                        {preset.label}
                      </option>
                    ))}
                  </select>
                  <p className="mt-1.5 text-xs text-neutral-500">
                    {getLlmProviderPreset(selectedPreset).description}
                  </p>
                </div>

                {/* 名称 */}
                <div>
                  <label
                    htmlFor="llm-provider-name"
                    className="block text-sm font-medium text-neutral-700 mb-2"
                  >
                    名称 <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="llm-provider-name"
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="例如：DeepSeek"
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    required
                  />
                </div>

                {/* 类型 */}
                <div>
                  <label
                    htmlFor="llm-provider-type"
                    className="block text-sm font-medium text-neutral-700 mb-2"
                  >
                    类型
                  </label>
                  <input
                    id="llm-provider-type"
                    type="text"
                    value={form.provider_type}
                    readOnly
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* Base URL */}
                <div className="md:col-span-2">
                  <label
                    htmlFor="llm-provider-base-url"
                    className="block text-sm font-medium text-neutral-700 mb-2"
                  >
                    Base URL
                  </label>
                  <input
                    id="llm-provider-base-url"
                    type="text"
                    value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    placeholder="https://api.deepseek.com/v1"
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* API Key */}
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    API Key{editingId ? "" : ""}
                  </label>
                  <input
                    type="password"
                    value={form.api_key}
                    onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                    placeholder={editingId ? "留空则不修改" : "sk-..."}
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                  {editingId && (
                    <p className="text-xs text-neutral-500 mt-1">
                      留空则保留原有 API Key
                    </p>
                  )}
                </div>

                {/* 模型列表 */}
                <div className="md:col-span-2">
                  <label
                    htmlFor="llm-provider-models"
                    className="block text-sm font-medium text-neutral-700 mb-2"
                  >
                    模型列表（逗号分隔）
                  </label>
                  <input
                    id="llm-provider-models"
                    type="text"
                    value={form.models}
                    onChange={(e) => setForm({ ...form, models: e.target.value })}
                    placeholder="deepseek-v3, deepseek-r1, deepseek-v4-pro"
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* 默认模型 */}
                <div>
                  <label
                    htmlFor="llm-provider-default-model"
                    className="block text-sm font-medium text-neutral-700 mb-2"
                  >
                    默认模型
                  </label>
                  <input
                    id="llm-provider-default-model"
                    type="text"
                    value={form.default_model}
                    onChange={(e) => setForm({ ...form, default_model: e.target.value })}
                    placeholder="deepseek-v3"
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* 优先级 */}
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    优先级
                  </label>
                  <input
                    type="number"
                    value={form.priority}
                    onChange={(e) =>
                      setForm({ ...form, priority: parseInt(e.target.value) || 100 })
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* 超时 */}
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    超时时间（秒）
                  </label>
                  <input
                    type="number"
                    value={form.timeout_seconds}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        timeout_seconds: parseInt(e.target.value) || 60,
                      })
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* 重试次数 */}
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    重试次数
                  </label>
                  <input
                    type="number"
                    value={form.retry_count}
                    onChange={(e) =>
                      setForm({ ...form, retry_count: parseInt(e.target.value) || 2 })
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* 启用开关 */}
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                    className="h-4 w-4 accent-neutral-950"
                  />
                  <label className="text-sm font-medium text-neutral-700">启用</label>
                </div>
              </div>

              <div className="flex gap-3">
                <Button type="submit" variant="primary" size="lg" isLoading={isSaving}>
                  {isSaving ? "保存中..." : editingId ? "更新 Provider" : "创建 Provider"}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  onClick={() => {
                    setShowForm(false);
                    setEditingId(null);
                  }}
                >
                  取消
                </Button>
              </div>
            </form>
          </Card>
        )}
    </PageShell>
  );
}
