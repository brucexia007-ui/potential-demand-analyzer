import { authenticatedFetch } from "@/lib/auth";


export type WatchTopic =
  | "COMPANY_PROFILE"
  | "PROCUREMENT"
  | "POLICY"
  | "CONTRACT_WINDOW"
  | "LEADERSHIP"
  | "PRODUCT_FIT";

export type WatchFrequency = "DAILY" | "WEEKLY" | "MONTHLY";

export type WatchSubscription = {
  id: string;
  target_account_id: string;
  capability_profile_id: string | null;
  root_skill_name: string;
  topics: WatchTopic[];
  frequency: WatchFrequency;
  timezone_name: string;
  max_external_calls: number;
  max_input_tokens: number;
  status: "ACTIVE" | "PAUSED" | "ARCHIVED";
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
};

export type WatchCheckRun = {
  id: string;
  subscription_id: string;
  target_account_id: string;
  previous_run_id: string | null;
  task_id: string | null;
  scheduled_for: string;
  analysis_as_of_date: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "PARTIAL" | "FAILED" | "SKIPPED_BUDGET";
  budget: Record<string, number>;
  usage: Record<string, number>;
  change_summary: {
    has_material_change?: boolean;
    new_evidence_count?: number;
    duplicate_evidence_count?: number;
    changed_claim_count?: number;
    gate_level?: string | null;
    categories?: Partial<Record<"procurement" | "policy" | "contract_window" | "claim", string[]>>;
  };
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

type SubscriptionList = { items: WatchSubscription[]; total: number };
type RunList = { items: WatchCheckRun[]; total: number };

async function watchlistRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `客户雷达请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function listWatchSubscriptions(targetAccountId: string): Promise<WatchSubscription[]> {
  const response = await watchlistRequest<SubscriptionList>(
    `/api/watchlist/subscriptions?target_account_id=${encodeURIComponent(targetAccountId)}`,
  );
  return response.items;
}

export function createWatchSubscription(payload: {
  target_account_id: string;
  capability_profile_id?: string;
  root_skill_name: string;
  topics: WatchTopic[];
  frequency: WatchFrequency;
  timezone_name: string;
  max_external_calls: number;
  max_input_tokens: number;
  start_immediately: boolean;
}): Promise<WatchSubscription> {
  return watchlistRequest("/api/watchlist/subscriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function updateWatchSubscription(
  subscriptionId: string,
  payload: {
    topics?: WatchTopic[];
    frequency?: WatchFrequency;
    timezone_name?: string;
    max_external_calls?: number;
    max_input_tokens?: number;
  },
): Promise<WatchSubscription> {
  return watchlistRequest(`/api/watchlist/subscriptions/${subscriptionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function pauseWatchSubscription(subscriptionId: string): Promise<WatchSubscription> {
  return watchlistRequest(`/api/watchlist/subscriptions/${subscriptionId}/pause`, { method: "POST" });
}

export function resumeWatchSubscription(subscriptionId: string): Promise<WatchSubscription> {
  return watchlistRequest(`/api/watchlist/subscriptions/${subscriptionId}/resume`, { method: "POST" });
}

export async function listWatchCheckRuns(subscriptionId: string, limit = 20): Promise<WatchCheckRun[]> {
  const response = await watchlistRequest<RunList>(
    `/api/watchlist/subscriptions/${subscriptionId}/runs?limit=${limit}`,
  );
  return response.items;
}
