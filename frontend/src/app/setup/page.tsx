"use client";

import { useEffect, useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/providers/auth-provider";
import { useConfig } from "@/components/providers/config-provider";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { authenticatedFetch } from "@/lib/auth";
import {
  getLlmProviderPreset,
  LLM_PROVIDER_PRESETS,
  type LlmProviderPresetKey,
} from "@/lib/llm-provider-presets";

const STEPS = [
  { key: "welcome", label: "欢迎", num: 1 },
  { key: "llm-config", label: "LLM 配置", num: 2 },
  { key: "llm-test", label: "LLM 测试", num: 3 },
  { key: "search-config", label: "搜索配置", num: 4 },
  { key: "search-test", label: "搜索测试", num: 5 },
  { key: "model-route", label: "模型路由", num: 6 },
  { key: "crawler", label: "抓取配置", num: 7 },
  { key: "budget", label: "预算限流", num: 8 },
  { key: "retention", label: "数据保留", num: 9 },
  { key: "done", label: "完成", num: 10 },
];

type ConfigStatus = {
  setup_completed: boolean;
  execution_ready: boolean;
  llm: { configured: boolean; verification_status: string; ready: boolean };
  search: { configured: boolean; verification_status: string; ready: boolean };
  model_routes_ready: boolean;
  blocking_items: Array<{ capability: string; status: string; action: string }>;
};

export default function SetupPage() {
  const router = useRouter();
  const { user, isLoading: authLoading, authState } = useAuth();
  const { markSetupCompleted } = useConfig();
  const { error: toastError, success: toastSuccess } = useToast();

  const [step, setStep] = useState(0);
  const [configStatus, setConfigStatus] = useState<ConfigStatus | null>(null);
  const [checkingStatus, setCheckingStatus] = useState(true);

  // LLM form
  const [llmPreset, setLlmPreset] =
    useState<LlmProviderPresetKey>("deepseek");
  const [llmForm, setLlmForm] = useState({
    ...getLlmProviderPreset("deepseek").values,
    api_key: "",
    enabled: true,
    priority: 100,
  });
  const [llmProviderId, setLlmProviderId] = useState<number | null>(null);
  const [llmTestResult, setLlmTestResult] = useState<{
    success: boolean;
    models?: string[];
    latency_ms?: number;
    error?: string;
  } | null>(null);
  const [isSavingLlm, setIsSavingLlm] = useState(false);
  const [isTestingLlm, setIsTestingLlm] = useState(false);

  // Search form
  const [searchForm, setSearchForm] = useState({
    name: "Bocha",
    provider_type: "bocha",
    api_key: "",
    base_url: "",
    appcode: "",
    app_key: "",
    app_secret: "",
    enabled: true,
    priority: 100,
    timeout_seconds: 30,
  });
  const [searchProviderId, setSearchProviderId] = useState<number | null>(null);
  const [searchTestResult, setSearchTestResult] = useState<{
    success: boolean;
    result_count?: number;
    latency_ms?: number;
    error?: string;
  } | null>(null);
  const [isSavingSearch, setIsSavingSearch] = useState(false);
  const [isTestingSearch, setIsTestingSearch] = useState(false);

  // v3.1: 新增步骤状态
  const [routePreset, setRoutePreset] = useState<"cheap" | "balanced" | "quality">("balanced");
  const [isSavingRoutePreset, setIsSavingRoutePreset] = useState(false);
  const [routeSummary, setRouteSummary] = useState<{ route_count: number; selected_model: string | null } | null>(null);
  const [crawlerConfig, setCrawlerConfig] = useState({ enable_static_fetch: true, enable_playwright_fetch: true, enable_field_agent: false, max_pages_per_task: 30, external_agent_step_limit: 20, external_agent_time_limit_seconds: 120 });
  const [budgetConfig, setBudgetConfig] = useState({ monthly_budget: "", per_task_budget: "", max_concurrent_tasks: 2, enable_adaptive_concurrency: true, allow_provider_fallback: true });
  const [retentionConfig, setRetentionConfig] = useState({ raw_web_text_days: 90, html_snapshot_days: 30, screenshot_days: 30, fetch_cache_days: 7, task_logs_days: 30, temp_files_days: 3 });
  const [completingSetup, setCompletingSetup] = useState(false);

  const apiHeaders = () => {
    return { "Content-Type": "application/json" };
  };

  const refreshConfigStatus = async () => {
    const response = await authenticatedFetch("/api/config/status");
    if (!response.ok) throw new Error("无法读取最新配置状态");
    const status: ConfigStatus = await response.json();
    setConfigStatus(status);
    return status;
  };

  // ── 检查配置状态 ──────────────────────────────────────────────────

  useEffect(() => {
    if (authLoading) return;
    if (authState === "unauthenticated") {
      router.push("/login?redirect=/setup");
      return;
    }
    if (!user) return;
    authenticatedFetch("/api/config/status")
      .then((r) => r.json())
      .then((status: ConfigStatus) => {
        setConfigStatus(status);
        if (status.execution_ready) {
          setStep(9);
        } else if (status.llm.ready && status.search.ready && !status.model_routes_ready) {
          setStep(5);
        } else if (status.setup_completed) {
          setStep(9);
        }
      })
      .catch(() => toastError("无法检查配置状态"))
      .finally(() => setCheckingStatus(false));
  }, [user, authLoading, authState, router, toastError]);

  // ── LLM 操作 ─────────────────────────────────────────────────────

  const applyLlmPreset = (key: LlmProviderPresetKey) => {
    const preset = getLlmProviderPreset(key);
    setLlmPreset(key);
    setLlmForm((current) => ({
      ...current,
      ...preset.values,
    }));
  };

  const saveLlmProvider = async (e: FormEvent) => {
    e.preventDefault();
    if (!llmForm.name.trim() || !llmForm.api_key.trim()) {
      toastError("请填写 LLM Provider 名称和 API Key");
      return;
    }
    setIsSavingLlm(true);
    try {
      const body = {
        name: llmForm.name.trim(),
        provider_type: llmForm.provider_type,
        base_url: llmForm.base_url.trim() || null,
        api_key: llmForm.api_key.trim(),
        models: llmForm.models.split(",").map((m) => m.trim()).filter(Boolean),
        default_model: llmForm.default_model.trim() || null,
        enabled: llmForm.enabled,
        priority: llmForm.priority,
        timeout_seconds: llmForm.timeout_seconds,
        retry_count: llmForm.retry_count,
      };
      const res = await authenticatedFetch("/api/config/providers", {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().then((d) => d.detail).catch(() => null);
        throw new Error(detail || "创建失败");
      }
      const created = await res.json();
      setLlmProviderId(created.id);
      toastSuccess("LLM Provider 已保存");
      setStep(2); // 自动进入测试步骤
    } catch (err) {
      toastError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setIsSavingLlm(false);
    }
  };

  const testLlmConnection = async () => {
    if (!llmProviderId) return;
    setIsTestingLlm(true);
    setLlmTestResult(null);
    try {
      const res = await authenticatedFetch(`/api/config/providers/${llmProviderId}/test`, {
        method: "POST",
        headers: apiHeaders(),
      });
      const result = await res.json();
      setLlmTestResult(result);
      if (result.success) await refreshConfigStatus();
    } catch {
      setLlmTestResult({ success: false, error: "请求失败" });
    } finally {
      setIsTestingLlm(false);
    }
  };

  // ── 搜索操作 ─────────────────────────────────────────────────────

  const saveSearchProvider = async (e: FormEvent) => {
    e.preventDefault();
    if (!searchForm.name.trim()) {
      toastError("请填写搜索 Provider 名称");
      return;
    }
    if (searchForm.provider_type === "bocha") {
      const hasApiKey = searchForm.api_key.trim();
      const hasAppcode = searchForm.appcode.trim();
      const hasSignAuth = hasAppcode && searchForm.app_key.trim() && searchForm.app_secret.trim();
      if (!hasApiKey && !hasAppcode && !hasSignAuth) {
        toastError("请填写 API Key、AppCode，或完整的签名鉴权信息（AppKey + AppSecret + AppCode）");
        return;
      }
    } else if (searchForm.provider_type !== "duckduckgo" && !searchForm.api_key.trim()) {
      toastError("请填写 API Key（DuckDuckGo 除外）");
      return;
    }
    setIsSavingSearch(true);
    try {
      const body = {
        name: searchForm.name.trim(),
        provider_type: searchForm.provider_type,
        api_key: searchForm.api_key.trim() || null,
        base_url: searchForm.base_url.trim() || null,
        appcode: searchForm.appcode.trim() || null,
        app_key: searchForm.app_key.trim() || null,
        app_secret: searchForm.app_secret.trim() || null,
        enabled: searchForm.enabled,
        priority: searchForm.priority,
        timeout_seconds: searchForm.timeout_seconds,
      };
      const res = await authenticatedFetch("/api/config/search", {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().then((d) => d.detail).catch(() => null);
        throw new Error(detail || "创建失败");
      }
      const created = await res.json();
      setSearchProviderId(created.id);
      toastSuccess("搜索 Provider 已保存");
      setStep(4); // 自动进入测试步骤
    } catch (err) {
      toastError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setIsSavingSearch(false);
    }
  };

  const testSearchConnection = async () => {
    if (!searchProviderId) return;
    setIsTestingSearch(true);
    setSearchTestResult(null);
    try {
      const res = await authenticatedFetch(`/api/config/search/${searchProviderId}/test`, {
        method: "POST",
        headers: apiHeaders(),
      });
      const result = await res.json();
      setSearchTestResult(result);
      if (result.success) await refreshConfigStatus();
    } catch {
      setSearchTestResult({ success: false, error: "请求失败" });
    } finally {
      setIsTestingSearch(false);
    }
  };

  const saveRoutePresetAndContinue = async () => {
    setIsSavingRoutePreset(true);
    try {
      const response = await authenticatedFetch("/api/config/model-routes-preset", {
        method: "PUT",
        headers: apiHeaders(),
        body: JSON.stringify({ preset: routePreset }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = typeof result.detail === "string" ? result.detail : result.detail?.message;
        throw new Error(message || "模型路由保存失败");
      }
      if (!result.route_count) {
        throw new Error("未创建可执行的模型路由");
      }
      setRouteSummary({
        route_count: result.route_count,
        selected_model: result.selected_model || null,
      });
      const status = await refreshConfigStatus();
      if (!status.model_routes_ready) {
        throw new Error("模型路由尚未生效，请重试");
      }
      toastSuccess("模型路由已创建");
      setStep(6);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "模型路由保存失败");
    } finally {
      setIsSavingRoutePreset(false);
    }
  };

  // ── Setup 完成 ─────────────────────────────────────────────────────

  // 安全解析预算数值，避免 parseFloat("0") || null 把 0 变成 null
  const parseBudget = (v: string): number | null => {
    if (v === "" || v.trim() === "") return null;
    const n = parseFloat(v);
    return isNaN(n) ? null : n;
  };

  // 配置保存端点名称映射（用于错误信息）
  const SAVE_ENDPOINT_LABELS: Record<number, string> = {
    0: "模型路由",
    1: "抓取配置",
    2: "预算配置",
    3: "数据保留",
  };

  const finishSetup = async (mode: "READY" | "BROWSE_ONLY") => {
    setCompletingSetup(true);
    try {
      // Step 6-9: 保存配置到后端（并行）
      const budgetPayload = {
        ...budgetConfig,
        monthly_budget: parseBudget(budgetConfig.monthly_budget),
        per_task_budget: parseBudget(budgetConfig.per_task_budget),
      };

      const saveResults = await Promise.allSettled([
        authenticatedFetch("/api/config/model-routes-preset", {
          method: "PUT",
          headers: apiHeaders(),
          body: JSON.stringify({ preset: routePreset }),
        }),
        authenticatedFetch("/api/config/crawler", {
          method: "PUT",
          headers: apiHeaders(),
          body: JSON.stringify(crawlerConfig),
        }),
        authenticatedFetch("/api/config/budget", {
          method: "PUT",
          headers: apiHeaders(),
          body: JSON.stringify(budgetPayload),
        }),
        authenticatedFetch("/api/config/data-retention", {
          method: "PUT",
          headers: apiHeaders(),
          body: JSON.stringify(retentionConfig),
        }),
      ]);

      // 收集失败项详情
      const failedDetails: string[] = [];
      saveResults.forEach((r, idx) => {
        if (r.status === "rejected") {
          failedDetails.push(`${SAVE_ENDPOINT_LABELS[idx] || `配置${idx + 1}`}(网络错误)`);
        } else if (r.status === "fulfilled" && !r.value.ok) {
          failedDetails.push(`${SAVE_ENDPOINT_LABELS[idx] || `配置${idx + 1}`}(${r.value.status})`);
        }
      });

      if (failedDetails.length > 0) {
        throw new Error(`${failedDetails.length} 项配置保存失败：${failedDetails.join("、")}，请重试`);
      }

      // 标记 Setup 完成
      const res = await authenticatedFetch("/api/config/setup-complete", {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({ mode }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        const message = typeof d.detail === "string" ? d.detail : d.detail?.message;
        throw new Error(message || "标记失败");
      }
      await markSetupCompleted();
      toastSuccess(mode === "READY" ? "配置完成，系统已就绪" : "已进入浏览模式");
      router.replace("/");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "标记完成状态失败，请重试");
    } finally {
      setCompletingSetup(false);
    }
  };

  // ── Loading ───────────────────────────────────────────────────────

  if (authLoading || checkingStatus) {
    return (
      <main className="min-h-screen pb-12">
        <div className="mx-auto flex max-w-2xl justify-center px-4 py-12">
          <div className="mr-3 h-6 w-6 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
          <span className="text-neutral-600">加载中...</span>
        </div>
      </main>
    );
  }

  // ── 步骤指示器 ────────────────────────────────────────────────────

  const Stepper = () => (
    <div className="mb-10 flex items-center justify-center overflow-x-auto">
      {STEPS.map((s, i) => {
        const isClickable = i < step; // 已完成的步骤可点击跳转
        return (
          <div key={s.key} className="flex items-center">
            <div className="flex flex-col items-center">
              <div
                onClick={isClickable ? () => setStep(i) : undefined}
                className={`flex h-8 w-8 items-center justify-center rounded-full border text-sm font-medium transition-colors ${
                  i < step
                    ? "border-neutral-950 bg-neutral-950 text-white cursor-pointer hover:bg-neutral-800"
                    : i === step
                    ? "border-neutral-950 bg-white text-neutral-950"
                    : "border-neutral-950/20 bg-white/70 text-neutral-400"
                }`}
                title={isClickable ? `返回 ${s.label}` : undefined}
                role={isClickable ? "button" : undefined}
              >
                {i < step ? "OK" : s.num}
              </div>
              <span
                className={`text-xs mt-1.5 ${
                  i <= step ? "font-medium text-neutral-950" : "text-neutral-400"
                }`}
              >
                {s.label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div
                className={`mx-1 mt-[-16px] h-0.5 w-8 sm:w-16 ${
                  i < step ? "bg-neutral-950" : "bg-neutral-950/10"
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );

  // ── 渲染各步骤 ────────────────────────────────────────────────────

  const renderStep = () => {
    switch (step) {
      // ── Step 1: 欢迎 ──────────────────────────────────────────
      case 0:
        return (
          <Card variant="bordered" padding="lg">
            <div className="text-center py-4">
              <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-lg border border-neutral-950 bg-neutral-950 text-xs font-semibold text-[var(--signal-lime)]">
                INIT
              </div>
              <h2 className="mb-4 text-xl font-semibold text-neutral-950">
                欢迎使用潜在需求分析系统
              </h2>
              <p className="mx-auto mb-6 max-w-md text-neutral-600">
                在开始之前，需要配置以下两项：
              </p>
              <div className="mx-auto mb-8 grid max-w-lg grid-cols-1 gap-4 text-left sm:grid-cols-2">
                <div className="rounded-lg border border-neutral-950/10 bg-white/75 p-4">
                  <div className="mb-3 h-1.5 w-8 rounded-full bg-[var(--signal-cyan)]" />
                  <div className="mb-1 font-medium text-neutral-950">LLM Provider</div>
                  <p className="text-sm text-neutral-600">
                    大语言模型 API，用于智能分析和报告生成
                  </p>
                </div>
                <div className="rounded-lg border border-neutral-950/10 bg-white/75 p-4">
                  <div className="mb-3 h-1.5 w-8 rounded-full bg-[var(--signal-lime)]" />
                  <div className="mb-1 font-medium text-neutral-950">搜索 Provider</div>
                  <p className="text-sm text-neutral-600">
                    搜索 API，用于获取互联网公开信息
                  </p>
                </div>
              </div>
              <Button variant="primary" size="lg" onClick={() => setStep(1)}>
                开始配置
              </Button>
              <p className="text-sm text-neutral-400 mt-4">
                已有配置？<a href="/settings/providers" className="text-neutral-950 underline underline-offset-4 hover:text-neutral-600">前往设置页</a>
              </p>
            </div>
          </Card>
        );

      // ── Step 2: LLM 配置 ────────────────────────────────────────
      case 1:
        return (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-6">
              配置 LLM Provider
            </h2>
            <form onSubmit={saveLlmProvider}>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                    接口预设
                  </label>
                  <select
                    aria-label="接口预设"
                    value={llmPreset}
                    onChange={(e) =>
                      applyLlmPreset(e.target.value as LlmProviderPresetKey)
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
                    {getLlmProviderPreset(llmPreset).description}
                  </p>
                </div>
                <div>
                  <label
                    htmlFor="setup-llm-name"
                    className="block text-sm font-medium text-neutral-700 mb-1.5"
                  >
                    名称 <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="setup-llm-name"
                    type="text"
                    value={llmForm.name}
                    onChange={(e) => setLlmForm({ ...llmForm, name: e.target.value })}
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    required
                  />
                </div>
                <div>
                  <label
                    htmlFor="setup-llm-base-url"
                    className="block text-sm font-medium text-neutral-700 mb-1.5"
                  >
                    Base URL <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="setup-llm-base-url"
                    type="text"
                    value={llmForm.base_url}
                    onChange={(e) => setLlmForm({ ...llmForm, base_url: e.target.value })}
                    placeholder="https://api.deepseek.com/v1"
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    required
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                    API Key <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="password"
                    value={llmForm.api_key}
                    onChange={(e) => setLlmForm({ ...llmForm, api_key: e.target.value })}
                    placeholder="sk-..."
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    required
                  />
                </div>
                <div className="md:col-span-2">
                  <label
                    htmlFor="setup-llm-models"
                    className="block text-sm font-medium text-neutral-700 mb-1.5"
                  >
                    模型列表（逗号分隔）
                  </label>
                  <input
                    id="setup-llm-models"
                    type="text"
                    value={llmForm.models}
                    onChange={(e) => setLlmForm({ ...llmForm, models: e.target.value })}
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>
                <div>
                  <label
                    htmlFor="setup-llm-default-model"
                    className="block text-sm font-medium text-neutral-700 mb-1.5"
                  >
                    默认模型
                  </label>
                  <input
                    id="setup-llm-default-model"
                    type="text"
                    value={llmForm.default_model}
                    onChange={(e) =>
                      setLlmForm({ ...llmForm, default_model: e.target.value })
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>
              </div>
              <div className="flex gap-3">
                <Button type="submit" variant="primary" size="lg" isLoading={isSavingLlm}>
                  {isSavingLlm ? "保存中..." : "保存并测试连接"}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  onClick={() => setStep(0)}
                >
                  上一步
                </Button>
              </div>
            </form>
          </Card>
        );

      // ── Step 3: LLM 测试 ────────────────────────────────────────
      case 2:
        return (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-6">
              测试 LLM 连接
            </h2>
            <p className="text-sm text-neutral-600 mb-4">
              将使用刚才保存的配置向 API 发起一次连接测试
            </p>

            {!llmTestResult && !isTestingLlm && (
              <Button variant="primary" onClick={testLlmConnection}>
                开始测试
              </Button>
            )}
            {!llmTestResult && isTestingLlm && (
              <div className="flex items-center gap-3 text-neutral-600">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
                正在测试连接...
              </div>
            )}

            {llmTestResult && (
              <div
                className={`mb-6 rounded-lg p-4 ${
                  llmTestResult.success
                    ? "bg-green-50 border border-green-200"
                    : "bg-red-50 border border-red-200"
                }`}
              >
                {llmTestResult.success ? (
                  <div>
                    <p className="text-lg font-medium text-green-700">连接成功</p>
                    <p className="text-green-600 mt-2">
                      可用模型: {(llmTestResult.models || []).join(", ")}
                    </p>
                    <p className="text-green-500 text-sm mt-1">
                      耗时: {llmTestResult.latency_ms}ms
                    </p>
                  </div>
                ) : (
                  <div>
                    <p className="text-lg font-medium text-red-700">连接失败</p>
                    <p className="text-red-600 mt-2">{llmTestResult.error}</p>
                  </div>
                )}
              </div>
            )}

            <div className="flex gap-3 mt-4">
              <Button
                variant="primary"
                size="lg"
                onClick={() => setStep(llmTestResult?.success ? 3 : 1)}
              >
                {llmTestResult?.success ? "下一步：配置搜索" : "返回修改"}
              </Button>
              <Button
                variant="secondary"
                size="lg"
                onClick={() => setStep(1)}
              >
                上一步
              </Button>
              {llmTestResult && !llmTestResult.success && (
                <Button variant="ghost" size="lg" onClick={() => finishSetup("BROWSE_ONLY")}>
                  稍后配置，进入浏览模式
                </Button>
              )}
            </div>
          </Card>
        );

      // ── Step 4: 搜索配置 ────────────────────────────────────────
      case 3:
        return (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-6">
              配置搜索 Provider
            </h2>
            <form onSubmit={saveSearchProvider}>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                    名称 <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={searchForm.name}
                    onChange={(e) =>
                      setSearchForm({ ...searchForm, name: e.target.value })
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    required
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                    搜索类型
                  </label>
                  <select
                    value={searchForm.provider_type}
                    onChange={(e) => {
                      const providerType = e.target.value;
                      const defaultNames: Record<string, string> = {
                        bocha: "Bocha",
                        bing: "Bing",
                        tavily: "Tavily",
                        duckduckgo: "DuckDuckGo",
                        custom: "自定义搜索",
                      };
                      setSearchForm({
                        ...searchForm,
                        provider_type: providerType,
                        name: defaultNames[providerType],
                      });
                    }}
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  >
                    <option value="bocha">Bocha 博查</option>
                    <option value="bing">Bing</option>
                    <option value="tavily">Tavily</option>
                    <option value="duckduckgo">DuckDuckGo</option>
                    <option value="custom">自定义</option>
                  </select>
                </div>
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                    API Key
                    {searchForm.provider_type === "duckduckgo" && (
                      <span className="text-neutral-400 ml-1">（DuckDuckGo 免 Key）</span>
                    )}
                    {searchForm.provider_type === "bocha" && (
                      <span className="text-neutral-400 ml-1">（与 AppCode 二选一）</span>
                    )}
                  </label>
                  <input
                    type="password"
                    value={searchForm.api_key}
                    onChange={(e) =>
                      setSearchForm({ ...searchForm, api_key: e.target.value })
                    }
                    placeholder={
                      searchForm.provider_type === "duckduckgo"
                        ? "无需填写"
                        : searchForm.provider_type === "bocha" && searchForm.appcode.trim()
                        ? "已填写 AppCode，可留空"
                        : "API Key"
                    }
                    disabled={searchForm.provider_type === "duckduckgo"}
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10 disabled:bg-neutral-100"
                  />
                </div>

                {searchForm.provider_type === "bocha" && (
                  <div className="md:col-span-2">
                    <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                      AppCode
                      <span className="text-neutral-400 ml-1">（阿里云市场购买时使用）</span>
                    </label>
                    <input
                      type="password"
                      value={searchForm.appcode}
                      onChange={(e) =>
                        setSearchForm({ ...searchForm, appcode: e.target.value })
                      }
                      placeholder={
                        searchForm.api_key.trim()
                          ? "已填写 API Key，可留空"
                          : "阿里云 APPCODE"
                      }
                      className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    />
                  </div>
                )}

                {searchForm.provider_type === "bocha" && (
                  <div className="md:col-span-2">
                    <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                      AppKey
                      <span className="text-neutral-400 ml-1">（阿里云 API 网关签名鉴权，选填）</span>
                    </label>
                    <input
                      type="password"
                      value={searchForm.app_key}
                      onChange={(e) =>
                        setSearchForm({ ...searchForm, app_key: e.target.value })
                      }
                      placeholder="阿里云 AppKey"
                      className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    />
                  </div>
                )}

                {searchForm.provider_type === "bocha" && (
                  <div className="md:col-span-2">
                    <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                      AppSecret
                      <span className="text-neutral-400 ml-1">（阿里云 API 网关签名鉴权，选填）</span>
                    </label>
                    <input
                      type="password"
                      value={searchForm.app_secret}
                      onChange={(e) =>
                        setSearchForm({ ...searchForm, app_secret: e.target.value })
                      }
                      placeholder="阿里云 AppSecret"
                      className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                    />
                  </div>
                )}

                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                    Base URL（可选）
                  </label>
                  <input
                    type="text"
                    value={searchForm.base_url}
                    onChange={(e) =>
                      setSearchForm({ ...searchForm, base_url: e.target.value })
                    }
                    placeholder={
                      searchForm.provider_type === "bocha"
                        ? "阿里云网关地址或留空使用默认"
                        : "留空使用默认地址"
                    }
                    className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
                  />
                </div>
              </div>
              <div className="flex gap-3">
                <Button
                  type="submit"
                  variant="primary"
                  size="lg"
                  isLoading={isSavingSearch}
                >
                  {isSavingSearch ? "保存中..." : "保存并测试搜索"}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  onClick={() => setStep(2)}
                >
                  上一步
                </Button>
              </div>
            </form>
          </Card>
        );

      // ── Step 5: 搜索测试 ────────────────────────────────────────
      case 4:
        return (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-6">
              测试搜索连接
            </h2>
            <p className="text-sm text-neutral-600 mb-4">
              将使用刚才保存的搜索配置发起一次搜索测试
            </p>

            {!searchTestResult && !isTestingSearch && (
              <Button variant="primary" onClick={testSearchConnection}>
                开始测试
              </Button>
            )}
            {!searchTestResult && isTestingSearch && (
              <div className="flex items-center gap-3 text-neutral-600">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
                正在测试搜索...
              </div>
            )}

            {searchTestResult && (
              <div
                className={`mb-6 rounded-lg p-4 ${
                  searchTestResult.success
                    ? "bg-green-50 border border-green-200"
                    : "bg-red-50 border border-red-200"
                }`}
              >
                {searchTestResult.success ? (
                  <div>
                    <p className="text-lg font-medium text-green-700">搜索测试成功</p>
                    <p className="text-green-600 mt-2">
                      返回 {searchTestResult.result_count} 条结果
                    </p>
                    <p className="text-green-500 text-sm mt-1">
                      耗时: {searchTestResult.latency_ms}ms
                    </p>
                  </div>
                ) : (
                  <div>
                    <p className="text-lg font-medium text-red-700">搜索测试失败</p>
                    <p className="text-red-600 mt-2">{searchTestResult.error}</p>
                  </div>
                )}
              </div>
            )}

            <div className="flex gap-3 mt-4">
              <Button
                variant="primary"
                size="lg"
                onClick={() => setStep(searchTestResult?.success ? 5 : 3)}
              >
                {searchTestResult?.success ? "下一步：完成" : "返回修改"}
              </Button>
              <Button
                variant="secondary"
                size="lg"
                onClick={() => setStep(3)}
              >
                上一步
              </Button>
              {searchTestResult && !searchTestResult.success && (
                <Button variant="ghost" size="lg" onClick={() => finishSetup("BROWSE_ONLY")}>
                  稍后配置，进入浏览模式
                </Button>
              )}
            </div>
          </Card>
        );

      // ── Step 6: 模型路由 ──────────────────────────────────────────
      case 5:
        return (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-4">模型路由配置</h2>
            <p className="text-sm text-neutral-600 mb-6">选择预设后，系统会使用已通过验证的 LLM Provider 默认模型创建低、中、高三档执行路由。</p>

            <div className="space-y-4 mb-6">
              {([
                { key: "cheap", label: "省钱模式", desc: "优先使用便宜模型，控制成本", color: "bg-green-50 border-green-200" },
                { key: "balanced", label: "均衡模式", desc: "成本和效果平衡，推荐默认使用", color: "bg-blue-50 border-blue-200" },
                { key: "quality", label: "高质量模式", desc: "优先使用最强模型，报告质量优先", color: "bg-purple-50 border-purple-200" },
              ] as const).map(({ key, label, desc, color }) => (
                <label
                  key={key}
                  className={`flex cursor-pointer items-start gap-4 rounded-lg border p-4 transition-all ${color} ${
                    routePreset === key ? "ring-2 ring-neutral-950" : "opacity-75 hover:opacity-100"
                  }`}
                >
                  <input
                    type="radio"
                    name="routePreset"
                    value={key}
                    checked={routePreset === key}
                    onChange={() => setRoutePreset(key)}
                    className="mt-0.5 accent-neutral-950"
                  />
                  <div>
                    <div className="font-medium text-neutral-900">{label}</div>
                    <div className="text-sm text-neutral-600">{desc}</div>
                  </div>
                </label>
              ))}
            </div>

            {routeSummary && (
              <div className="mb-6 rounded-lg border border-green-200 bg-green-50 p-4 text-sm text-green-800">
                已创建 {routeSummary.route_count} 条模型路由
                {routeSummary.selected_model ? `，默认模型：${routeSummary.selected_model}` : "，保留现有人工路由"}。
              </div>
            )}

            <div className="flex gap-3">
              <Button variant="primary" size="lg" onClick={saveRoutePresetAndContinue} isLoading={isSavingRoutePreset}>
                {isSavingRoutePreset ? "创建路由中..." : "保存路由并继续"}
              </Button>
              <Button variant="secondary" size="lg" onClick={() => setStep(4)}>上一步</Button>
            </div>
          </Card>
        );

      // ── Step 7: 抓取配置 ──────────────────────────────────────────
      case 6:
        return (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-4">抓取与外部 Agent 配置</h2>
            <p className="text-sm text-neutral-600 mb-6">配置网页抓取能力和体验式背调 Agent（可跳过，后续在设置中修改）。</p>

            {routeSummary && (
              <div className="mb-6 rounded-lg border border-green-200 bg-green-50 p-4 text-sm text-green-800">
                模型路由已创建：{routeSummary.route_count} 条
                {routeSummary.selected_model ? `，默认模型：${routeSummary.selected_model}` : "，保留现有人工路由"}。
              </div>
            )}

            <div className="space-y-4 mb-6">
              <label className="flex items-center justify-between">
                <div><span className="font-medium text-neutral-800">启用静态抓取</span><p className="text-sm text-neutral-500">HTTP 请求获取网页内容</p></div>
                <input type="checkbox" checked={crawlerConfig.enable_static_fetch} onChange={(e) => setCrawlerConfig({ ...crawlerConfig, enable_static_fetch: e.target.checked })} className="h-4 w-4 accent-neutral-950" />
              </label>
              <label className="flex items-center justify-between">
                <div><span className="font-medium text-neutral-800">启用动态抓取 (Playwright)</span><p className="text-sm text-neutral-500">浏览器渲染 JS 页面</p></div>
                <input type="checkbox" checked={crawlerConfig.enable_playwright_fetch} onChange={(e) => setCrawlerConfig({ ...crawlerConfig, enable_playwright_fetch: e.target.checked })} className="h-4 w-4 accent-neutral-950" />
              </label>
              <label className="flex items-center justify-between">
                <div><span className="font-medium text-neutral-800">允许体验式背调</span><p className="text-sm text-neutral-500">PlaywrightFieldAgent 公开网页观察</p></div>
                <input type="checkbox" checked={crawlerConfig.enable_field_agent} onChange={(e) => setCrawlerConfig({ ...crawlerConfig, enable_field_agent: e.target.checked })} className="h-4 w-4 accent-neutral-950" />
              </label>
            </div>

            <div className="flex gap-3">
              <Button variant="primary" size="lg" onClick={() => setStep(7)}>下一步：预算配置</Button>
              <Button variant="secondary" size="lg" onClick={() => setStep(5)}>上一步</Button>
              <Button variant="ghost" size="lg" onClick={() => setStep(7)}>跳过</Button>
            </div>
          </Card>
        );

      // ── Step 8: 预算配置 ──────────────────────────────────────────
      case 7:
        return (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-4">预算与限流配置</h2>
            <p className="text-sm text-neutral-600 mb-6">设置预算限制和并发策略（可跳过，留空表示无限制）。</p>

            <div className="space-y-4 mb-6">
              <div>
                <label className="mb-1.5 block text-sm font-medium text-neutral-700">月度预算上限（留空=无限制）</label>
                <input type="number" min={0} step={0.01} value={budgetConfig.monthly_budget}
                  onChange={(e) => setBudgetConfig({ ...budgetConfig, monthly_budget: e.target.value })}
                  placeholder="无限制" className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5" />
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-neutral-700">单任务预算上限（留空=无限制）</label>
                <input type="number" min={0} step={0.01} value={budgetConfig.per_task_budget}
                  onChange={(e) => setBudgetConfig({ ...budgetConfig, per_task_budget: e.target.value })}
                  placeholder="无限制" className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5" />
              </div>
              <label className="flex items-center justify-between">
                <div><span className="font-medium text-neutral-800">自适应并发</span><p className="text-sm text-neutral-500">429 后自动降低并发</p></div>
                <input type="checkbox" checked={budgetConfig.enable_adaptive_concurrency} onChange={(e) => setBudgetConfig({ ...budgetConfig, enable_adaptive_concurrency: e.target.checked })} className="h-4 w-4 accent-neutral-950" />
              </label>
              <label className="flex items-center justify-between">
                <div><span className="font-medium text-neutral-800">Provider 自动降级</span><p className="text-sm text-neutral-500">主 Provider 不可用时切换备用</p></div>
                <input type="checkbox" checked={budgetConfig.allow_provider_fallback} onChange={(e) => setBudgetConfig({ ...budgetConfig, allow_provider_fallback: e.target.checked })} className="h-4 w-4 accent-neutral-950" />
              </label>
            </div>

            <div className="flex gap-3">
              <Button variant="primary" size="lg" onClick={() => setStep(8)}>下一步：数据保留</Button>
              <Button variant="secondary" size="lg" onClick={() => setStep(6)}>上一步</Button>
              <Button variant="ghost" size="lg" onClick={() => setStep(8)}>跳过</Button>
            </div>
          </Card>
        );

      // ── Step 9: 数据保留 ──────────────────────────────────────────
      case 8:
        return (
          <Card variant="bordered" padding="lg">
            <h2 className="text-lg font-semibold text-neutral-900 mb-4">数据保留策略</h2>
            <p className="text-sm text-neutral-600 mb-6">设置各类数据保留天数（可跳过，使用默认值）。</p>

            <div className="space-y-3 mb-6">
              {[
                { label: "原始网页文本", key: "raw_web_text_days" as const, def: 90 },
                { label: "HTML 快照", key: "html_snapshot_days" as const, def: 30 },
                { label: "页面截图", key: "screenshot_days" as const, def: 30 },
                { label: "抓取缓存", key: "fetch_cache_days" as const, def: 7 },
                { label: "任务日志", key: "task_logs_days" as const, def: 30 },
                { label: "临时文件", key: "temp_files_days" as const, def: 3 },
              ].map(({ label, key, def }) => (
                <div key={key} className="flex items-center justify-between rounded-lg border border-neutral-950/10 px-4 py-2.5">
                  <span className="text-sm font-medium text-neutral-700">{label}</span>
                  <div className="flex items-center gap-2">
                    <input type="range" min={1} max={365} value={retentionConfig[key]}
                      onChange={(e) => setRetentionConfig({ ...retentionConfig, [key]: parseInt(e.target.value) })}
                      className="w-24 accent-neutral-950" />
                    <span className="text-sm font-semibold text-neutral-950 w-16 text-right">{retentionConfig[key]} 天</span>
                    <button type="button" className="text-xs text-neutral-400 underline" onClick={() => setRetentionConfig({ ...retentionConfig, [key]: def })}>默认</button>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex gap-3">
              <Button variant="primary" size="lg" onClick={() => setStep(9)}>下一步：完成</Button>
              <Button variant="secondary" size="lg" onClick={() => setStep(7)}>上一步</Button>
              <Button variant="ghost" size="lg" onClick={() => setStep(9)}>跳过</Button>
            </div>
          </Card>
        );

      // ── Step 10: 完成 ────────────────────────────────────────────
      case 9:
        return (
          <Card variant="bordered" padding="lg">
            <div className="text-center py-4">
              <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-lg border border-neutral-950 bg-neutral-950 text-xs font-semibold text-[var(--signal-lime)]">
                {configStatus?.execution_ready ? "READY" : "BROWSE"}
              </div>
              <h2 className="mb-2 text-xl font-semibold text-neutral-950">
                {configStatus?.execution_ready ? "配置完成！" : "配置尚未就绪"}
              </h2>
              <p className="text-neutral-600 mb-8">
                {configStatus?.execution_ready
                  ? "系统已就绪，可以开始使用了"
                  : "你可以进入系统浏览，研究与批量执行将在连接验证通过后开放"}
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8 max-w-lg mx-auto text-left">
                {configStatus?.execution_ready ? (
                  <>
                    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
                      <div className="mb-1 font-medium text-green-800">LLM 已配置</div>
                      <p className="text-sm text-green-600">大语言模型 API 可用</p>
                    </div>
                    <div className="rounded-lg border border-green-200 bg-green-50 p-4">
                      <div className="mb-1 font-medium text-green-800">搜索已配置</div>
                      <p className="text-sm text-green-600">搜索 API 可用</p>
                    </div>
                    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
                      <div className="mb-1 font-medium text-blue-800">抓取已配置</div>
                      <p className="text-sm text-blue-600">网页抓取能力可用</p>
                    </div>
                    <div className="rounded-lg border border-purple-200 bg-purple-50 p-4">
                      <div className="mb-1 font-medium text-purple-800">预算已配置</div>
                      <p className="text-sm text-purple-600">成本控制策略已设</p>
                    </div>
                  </>
                ) : (
                  <div className="sm:col-span-2 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
                    <p className="text-sm text-yellow-700">
                      配置状态检查未完全通过，请到设置页面确认。
                    </p>
                  </div>
                )}
              </div>

              <div className="flex flex-col sm:flex-row gap-3 justify-center">
                <Button
                  variant="primary"
                  size="lg"
                  onClick={() => finishSetup("READY")}
                  disabled={completingSetup || !configStatus?.execution_ready}
                >
                  {completingSetup ? "保存中..." : "完成配置并开始使用"}
                </Button>
                {!configStatus?.execution_ready && (
                  <Button
                    variant="secondary"
                    size="lg"
                    onClick={() => finishSetup("BROWSE_ONLY")}
                    disabled={completingSetup}
                  >
                    稍后配置，进入浏览模式
                  </Button>
                )}
                <Button
                  variant="secondary"
                  size="lg"
                  onClick={() => router.push("/settings/providers")}
                >
                  进入设置
                </Button>
                <Button
                  variant="secondary"
                  size="lg"
                  onClick={() => setStep(8)}
                >
                  上一步
                </Button>
              </div>
            </div>
          </Card>
        );

      default:
        return null;
    }
  };

  return (
    <main className="min-h-screen pb-12">
      <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8 pt-8">
        <Stepper />
        {renderStep()}
      </div>
    </main>
  );
}
