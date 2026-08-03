"use client";

import { useEffect, useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

type SearchProvider = {
  id: number;
  name: string;
  provider_type: string;
  masked_api_key: string | null;
  masked_appcode: string | null;
  masked_app_key: string | null;
  masked_app_secret: string | null;
  base_url: string | null;
  enabled: boolean;
  priority: number;
  daily_limit: number | null;
  per_task_limit: number | null;
  timeout_seconds: number;
  created_at: string | null;
  updated_at: string | null;
};

type TestResult = {
  success: boolean;
  result_count?: number;
  latency_ms?: number;
  error?: string;
} | null;

const PROVIDER_TYPES = [
  { value: "bocha", label: "Bocha 博查" },
  { value: "bing", label: "Bing" },
  { value: "tavily", label: "Tavily" },
  { value: "duckduckgo", label: "DuckDuckGo" },
  { value: "custom", label: "自定义" },
];

const EMPTY_FORM = {
  name: "",
  provider_type: "bocha",
  api_key: "",
  base_url: "",
  appcode: "",
  app_key: "",
  app_secret: "",
  enabled: true,
  priority: 100,
  daily_limit: "",
  per_task_limit: "",
  timeout_seconds: 30,
};

export default function SearchProvidersPage() {
  const router = useRouter();
  const { error: toastError, success: toastSuccess } = useToast();

  const [providers, setProviders] = useState<SearchProvider[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
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
      const res = await authenticatedFetch("/api/config/search");
      if (!res.ok) throw new Error("加载失败");
      setProviders(await res.json());
    } catch {
      toastError("加载搜索 Provider 列表失败");
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
        for (const h of data.search || []) {
          map[h.provider_id] = { status: h.status, consecutive_429: h.consecutive_429 };
        }
        setHealthMap(map);
      }
    } catch { /* ignore */ }
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
    setForm(EMPTY_FORM);
    setShowForm(true);
  };

  const openEdit = (p: SearchProvider) => {
    setEditingId(p.id);
    setForm({
      name: p.name,
      provider_type: p.provider_type,
      api_key: "", // 编辑时不回填
      base_url: p.base_url || "",
      appcode: "", // 编辑时不回填
      app_key: "", // 编辑时不回填
      app_secret: "", // 编辑时不回填
      enabled: p.enabled,
      priority: p.priority,
      daily_limit: p.daily_limit?.toString() || "",
      per_task_limit: p.per_task_limit?.toString() || "",
      timeout_seconds: p.timeout_seconds,
    });
    setShowForm(true);
  };

  const isDuckDuckGo = form.provider_type === "duckduckgo";

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) {
      toastError("请填写名称");
      return;
    }

    setIsSaving(true);
    try {
      const body: Record<string, unknown> = {
        name: form.name.trim(),
        provider_type: form.provider_type,
        api_key: form.api_key.trim() || null,
        base_url: form.base_url.trim() || null,
        appcode: form.appcode.trim() || null,
        app_key: form.app_key.trim() || null,
        app_secret: form.app_secret.trim() || null,
        enabled: form.enabled,
        priority: form.priority,
        daily_limit: form.daily_limit ? parseInt(form.daily_limit) : null,
        per_task_limit: form.per_task_limit ? parseInt(form.per_task_limit) : null,
        timeout_seconds: form.timeout_seconds,
      };

      if (editingId && !form.api_key.trim()) {
        delete body.api_key;
      }
      if (editingId && !form.appcode.trim()) {
        delete body.appcode;
      }
      if (editingId && !form.app_key.trim()) {
        delete body.app_key;
      }
      if (editingId && !form.app_secret.trim()) {
        delete body.app_secret;
      }

      const url = editingId
        ? `/api/config/search/${editingId}`
        : "/api/config/search";
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

      toastSuccess(editingId ? "搜索 Provider 已更新" : "搜索 Provider 已创建");
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
    if (!confirm("确定要删除此搜索 Provider 吗？")) return;
    try {
      const res = await authenticatedFetch(`/api/config/search/${id}`, {
        method: "DELETE",
        headers: apiHeaders(),
      });
      if (!res.ok) throw new Error("删除失败");
      toastSuccess("搜索 Provider 已删除");
      await loadProviders();
    } catch (err) {
      toastError(err instanceof Error ? err.message : "删除失败");
    }
  };

  const handleTest = async (id: number) => {
    setTestingIds((prev) => new Set(prev).add(id));
    setTestResults((prev) => ({ ...prev, [id]: null }));
    try {
      const res = await authenticatedFetch(`/api/config/search/${id}/test`, {
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

  const providerTypeLabel = (type: string) => {
    const found = PROVIDER_TYPES.find((t) => t.value === type);
    return found ? found.label : type;
  };

  if (isLoading) {
    return (
      <main className="min-h-screen pb-12">
        <div className="mx-auto flex max-w-4xl justify-center px-4 py-12">
          <div className="mr-3 h-6 w-6 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
          <span className="text-neutral-600">加载搜索 Provider 列表...</span>
        </div>
      </main>
    );
  }

  return (
    <PageShell>
      <PageHeader
        eyebrow="SEARCH ROUTING"
        title="搜索 Provider 管理"
        description="配置搜索 API 提供商，支持多源按优先级回退"
        action={
          <>
            <Button variant="secondary" onClick={() => router.push("/settings/providers")}>
              LLM 设置
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
              <p className="text-neutral-500 mb-4">尚未配置任何搜索 Provider</p>
              <Button variant="primary" onClick={openCreate}>
                添加第一个搜索 Provider
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
                      {healthMap[p.id] && (
                        <span
                          className={`inline-flex px-2 py-0.5 text-xs rounded-full ${healthLabel(healthMap[p.id].status).color}`}
                          title={`连续429: ${healthMap[p.id].consecutive_429}`}
                        >
                          {healthLabel(healthMap[p.id].status).text}
                        </span>
                      )}
                      <span className="rounded-full border border-cyan-200 bg-cyan-50 px-2 py-0.5 text-xs text-cyan-700">
                        {providerTypeLabel(p.provider_type)}
                      </span>
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
                      <p>
                        API Key: {p.masked_api_key || (p.provider_type === "duckduckgo" ? "无需 Key" : "—")}
                        {p.provider_type === "bocha" && (
                          <> | AppCode: {p.masked_appcode || "—"} | AppKey: {p.masked_app_key || "—"} | AppSecret: {p.masked_app_secret || "—"}</>
                        )}
                        {" "}| Base URL: {p.base_url || "—"}
                      </p>
                      <p>
                        超时: {p.timeout_seconds}s
                        {p.daily_limit && ` | 每日限额: ${p.daily_limit}`}
                        {p.per_task_limit && ` | 每任务限额: ${p.per_task_limit}`}
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
                      {testingIds.has(p.id) ? "测试中..." : "测试搜索"}
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
                        <p className="text-green-700 font-medium">搜索测试成功</p>
                        <p className="text-green-600 mt-1">
                          返回 {testResults[p.id]!.result_count} 条结果
                        </p>
                        <p className="text-green-500 text-xs mt-1">
                          耗时: {testResults[p.id]!.latency_ms}ms
                        </p>
                      </div>
                    ) : (
                      <div>
                        <p className="text-red-700 font-medium">搜索测试失败</p>
                        <p className="text-red-600 mt-1">{testResults[p.id]!.error}</p>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}

        {providers.length > 0 && !showForm && (
          <Button variant="primary" onClick={openCreate}>
            添加搜索 Provider
          </Button>
        )}

        {/* 创建/编辑表单 */}
        {showForm && (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-6">
              {editingId ? "编辑搜索 Provider" : "新建搜索 Provider"}
            </h2>
            <form onSubmit={handleSave}>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
                {/* 名称 */}
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    名称 <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="例如：Bocha 主源"
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    required
                  />
                </div>

                {/* Provider 类型 */}
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    搜索类型
                  </label>
                  <select
                    value={form.provider_type}
                    onChange={(e) =>
                      setForm({ ...form, provider_type: e.target.value })
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  >
                    {PROVIDER_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </div>

                {/* API Key */}
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    API Key
                    {isDuckDuckGo && (
                      <span className="text-neutral-400 ml-1">（DuckDuckGo 不需要）</span>
                    )}
                  </label>
                  <input
                    type="password"
                    value={form.api_key}
                    onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                    placeholder={
                      isDuckDuckGo
                        ? "无需填写"
                        : editingId
                        ? "留空则不修改"
                        : "API Key"
                    }
                    disabled={isDuckDuckGo}
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10 disabled:bg-neutral-100 disabled:text-neutral-400"
                  />
                  {editingId && !isDuckDuckGo && (
                    <p className="text-xs text-neutral-500 mt-1">
                      留空则保留原有 API Key
                    </p>
                  )}
                </div>

                {/* AppCode（仅 Bocha） */}
                {form.provider_type === "bocha" && (
                  <div className="md:col-span-2">
                    <label className="block text-sm font-medium text-neutral-700 mb-2">
                      AppCode
                      <span className="text-neutral-400 ml-1">（阿里云市场购买时填写，与 API Key 二选一）</span>
                    </label>
                    <input
                      type="password"
                      value={form.appcode}
                      onChange={(e) => setForm({ ...form, appcode: e.target.value })}
                      placeholder={
                        editingId
                          ? "留空则保留原有 AppCode"
                          : form.api_key.trim()
                          ? "已填写 API Key，可留空"
                          : "阿里云 APPCODE"
                      }
                      className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    />
                    {editingId && (
                      <p className="text-xs text-neutral-500 mt-1">
                        留空则保留原有 AppCode
                      </p>
                    )}
                  </div>
                )}

                {/* AppKey（仅 Bocha，阿里云 API 网关签名鉴权） */}
                {form.provider_type === "bocha" && (
                  <div className="md:col-span-2">
                    <label className="block text-sm font-medium text-neutral-700 mb-2">
                      AppKey
                      <span className="text-neutral-400 ml-1">（阿里云 API 网关签名鉴权，选填）</span>
                    </label>
                    <input
                      type="password"
                      value={form.app_key}
                      onChange={(e) => setForm({ ...form, app_key: e.target.value })}
                      placeholder={editingId ? "留空则保留原有 AppKey" : "阿里云 AppKey"}
                      className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    />
                    {editingId && (
                      <p className="text-xs text-neutral-500 mt-1">
                        留空则保留原有 AppKey
                      </p>
                    )}
                  </div>
                )}

                {/* AppSecret（仅 Bocha，阿里云 API 网关签名鉴权） */}
                {form.provider_type === "bocha" && (
                  <div className="md:col-span-2">
                    <label className="block text-sm font-medium text-neutral-700 mb-2">
                      AppSecret
                      <span className="text-neutral-400 ml-1">（阿里云 API 网关签名鉴权，选填）</span>
                    </label>
                    <input
                      type="password"
                      value={form.app_secret}
                      onChange={(e) => setForm({ ...form, app_secret: e.target.value })}
                      placeholder={editingId ? "留空则保留原有 AppSecret" : "阿里云 AppSecret"}
                      className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    />
                    {editingId && (
                      <p className="text-xs text-neutral-500 mt-1">
                        留空则保留原有 AppSecret
                      </p>
                    )}
                  </div>
                )}

                {/* Base URL */}
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    Base URL（可选）
                  </label>
                  <input
                    type="text"
                    value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    placeholder={form.provider_type === "custom" ? "https://your-search-api.com/search" : "留空使用默认地址"}
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
                        timeout_seconds: parseInt(e.target.value) || 30,
                      })
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* 每日限额 */}
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    每日调用限额（可选）
                  </label>
                  <input
                    type="number"
                    value={form.daily_limit}
                    onChange={(e) => setForm({ ...form, daily_limit: e.target.value })}
                    placeholder="不限制"
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>

                {/* 每任务限额 */}
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-2">
                    每任务调用限额（可选）
                  </label>
                  <input
                    type="number"
                    value={form.per_task_limit}
                    onChange={(e) =>
                      setForm({ ...form, per_task_limit: e.target.value })
                    }
                    placeholder="不限制"
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
                  {isSaving ? "保存中..." : editingId ? "更新" : "创建"}
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
