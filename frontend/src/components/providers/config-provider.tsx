"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/components/providers/auth-provider";
import { authenticatedFetch } from "@/lib/auth";

// ── 类型定义 ──────────────────────────────────────────────────────────

export type ConfigStatus = {
  setup_completed: boolean;
  setup_mode: "READY" | "BROWSE_ONLY" | null;
  execution_ready: boolean;
  llm: CapabilityReadiness;
  search: CapabilityReadiness;
  model_routes_ready: boolean;
  blocking_items: BlockingItem[];
  warnings: string[];
};

export type CapabilityReadiness = {
  configured: boolean;
  verification_status: "UNTESTED" | "PASSED" | "FAILED" | "STALE";
  ready: boolean;
  last_tested_at: string | null;
  error_code: string | null;
  error_message: string | null;
  provider_count: number;
  configured_provider_count: number;
};

export type BlockingItem = {
  capability: "llm" | "search" | "model_routes";
  status: string;
  action: string;
};

type ConfigContextValue = {
  status: ConfigStatus | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  markSetupCompleted: () => Promise<void>;
};

// ── Context ────────────────────────────────────────────────────────────

const ConfigContext = createContext<ConfigContextValue>({
  status: null,
  isLoading: true,
  error: null,
  refresh: async () => {},
  markSetupCompleted: async () => {},
});

export function useConfig() {
  return useContext(ConfigContext);
}

// ── 无需跳转的路径 ─────────────────────────────────────────────────────

const SKIP_REDIRECT_PATHS = ["/setup", "/login", "/register"];

function shouldSkipRedirect(pathname: string): boolean {
  return SKIP_REDIRECT_PATHS.some(
    (p) => pathname === p || pathname.startsWith(p + "/")
  );
}

// ── Provider ────────────────────────────────────────────────────────────

export function ConfigProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, isLoading: authLoading } = useAuth();

  const [status, setStatus] = useState<ConfigStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      setError(null);
      const resp = await authenticatedFetch("/api/config/status");
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data: ConfigStatus = await resp.json();
      setStatus(data);
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : "未知错误";
      setError(msg);
      return null;
    }
  }, []);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    await fetchStatus();
    setIsLoading(false);
  }, [fetchStatus]);

  const markSetupCompleted = useCallback(async () => {
    // 通过刷新 status 来间接确认（后端在 setup 完成时会更新）
    await refresh();
  }, [refresh]);

  // ── 初始加载 ──────────────────────────────────────────────────────

  useEffect(() => {
    // 等待 auth 加载完成再检查配置状态
    if (authLoading) return;

    // 未登录时不检查（让 auth provider 处理重定向到 /login）
    if (!user) {
      setIsLoading(false);
      return;
    }

    fetchStatus().finally(() => setIsLoading(false));
  }, [authLoading, user, fetchStatus]);

  // ── 自动跳转逻辑 ──────────────────────────────────────────────────

  useEffect(() => {
    // 条件：已登录 + 状态已加载 + 非加载中 + 非跳过路径
    if (
      !user ||
      !status ||
      isLoading ||
      authLoading ||
      shouldSkipRedirect(pathname)
    ) {
      return;
    }

    // 如果 setup 未完成且不在跳过路径，跳转到 /setup
    if (!status.setup_completed) {
      router.replace("/setup");
    }
  }, [user, status, isLoading, authLoading, pathname, router]);

  return (
    <ConfigContext.Provider
      value={{ status, isLoading, error, refresh, markSetupCompleted }}
    >
      {user && status?.setup_completed && !status.execution_ready && (
        <div className="border-b border-amber-300 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900">
          当前为浏览模式，研究与批量执行暂不可用。
          <button className="ml-2 font-medium underline" onClick={() => router.push("/setup")}>
            继续完成配置
          </button>
        </div>
      )}
      {children}
    </ConfigContext.Provider>
  );
}
