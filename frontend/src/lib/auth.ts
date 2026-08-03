/** HttpOnly Cookie 认证客户端。 */

const SESSION_EXPIRED_EVENT = "auth:session-expired";

export interface User {
  id: string;
  username: string;
  is_active: boolean;
}

export interface SessionResponse {
  username: string;
  access_expires_in_seconds: number;
  session_expires_in_seconds: number;
}

export class AuthApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function sameOrigin(init: RequestInit = {}): RequestInit {
  return { ...init, credentials: "same-origin" };
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null);
  return body && typeof body.detail === "string" ? body.detail : fallback;
}

let refreshPromise: Promise<boolean> | null = null;

export function refreshSession(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = fetch("/api/auth/refresh", sameOrigin({ method: "POST" }))
    .then((response) => {
      if (response.ok) return true;
      if (response.status === 401) return false;
      throw new AuthApiError(response.status, "刷新登录会话失败");
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
}

function notifySessionExpired(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
}

export function onSessionExpired(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener(SESSION_EXPIRED_EVENT, listener);
  return () => window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
}

export async function authenticatedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const response = await fetch(input, sameOrigin(init));
  if (response.status !== 401) return response;

  const refreshed = await refreshSession();
  if (!refreshed) {
    notifySessionExpired();
    return response;
  }

  const retried = await fetch(input, sameOrigin(init));
  if (retried.status === 401) notifySessionExpired();
  return retried;
}

export async function login(username: string, password: string): Promise<SessionResponse> {
  const formData = new FormData();
  formData.append("username", username);
  formData.append("password", password);

  const response = await fetch("/api/auth/login", sameOrigin({ method: "POST", body: formData }));
  if (!response.ok) {
    throw new AuthApiError(
      response.status,
      await errorMessage(response, "登录失败，请检查用户名和密码"),
    );
  }
  return response.json();
}

export async function getSessionUser(): Promise<User | null> {
  const response = await authenticatedFetch("/api/auth/me");
  if (response.status === 401) return null;
  if (!response.ok) {
    throw new AuthApiError(response.status, await errorMessage(response, "读取登录状态失败"));
  }
  return response.json();
}

export async function logout(): Promise<void> {
  const response = await fetch("/api/auth/logout", sameOrigin({ method: "POST" }));
  if (!response.ok && response.status !== 401) {
    throw new AuthApiError(response.status, await errorMessage(response, "退出登录失败"));
  }
}
