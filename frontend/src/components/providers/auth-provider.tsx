"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  getSessionUser,
  logout as logoutSession,
  onSessionExpired,
  type User,
} from "@/lib/auth";

export type AuthState = "loading" | "authenticated" | "unauthenticated" | "unavailable";

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  authState: AuthState;
  refreshUser: () => Promise<User>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [authState, setAuthState] = useState<AuthState>("loading");

  const fetchUser = useCallback(async (): Promise<User | null> => {
    try {
      const current = await getSessionUser();
      setUser(current);
      setAuthState(current ? "authenticated" : "unauthenticated");
      return current;
    } catch (error) {
      setAuthState("unavailable");
      throw error;
    }
  }, []);

  useEffect(() => {
    void fetchUser().catch(() => undefined);
  }, [fetchUser]);

  useEffect(() => onSessionExpired(() => {
    setUser(null);
    setAuthState("unauthenticated");
  }), []);

  useEffect(() => {
    const protectedRoutes = ["/", "/history", "/tasks", "/settings", "/batches"];
    const isProtectedRoute = protectedRoutes.some(
      (route) => pathname === route || (route !== "/" && pathname.startsWith(`${route}/`)),
    );
    if (authState === "unauthenticated" && isProtectedRoute) {
      router.replace(`/login?redirect=${encodeURIComponent(pathname)}`);
    }
  }, [authState, pathname, router]);

  const refreshUser = useCallback(async (): Promise<User> => {
    const current = await fetchUser();
    if (!current) {
      throw new Error("登录会话未建立");
    }
    return current;
  }, [fetchUser]);

  const logout = useCallback(async () => {
    await logoutSession();
    setUser(null);
    setAuthState("unauthenticated");
  }, []);

  return (
    <AuthContext.Provider value={{
      user,
      isLoading: authState === "loading",
      isAuthenticated: authState === "authenticated",
      authState,
      refreshUser,
      logout,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within an AuthProvider");
  return context;
}
