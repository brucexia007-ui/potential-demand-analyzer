import "./globals.css";
import type { ReactNode } from "react";
import { AuthProvider } from "@/components/providers/auth-provider";
import { ConfigProvider } from "@/components/providers/config-provider";
import { ToastProvider } from "@/components/ui/toast";
import { ErrorBoundary } from "@/components/error-boundary";
import { NetworkStatus } from "@/components/network-status";
import { Header } from "@/components/layout/header";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen font-sans antialiased">
        <ErrorBoundary>
          <ToastProvider>
            <AuthProvider>
              <ConfigProvider>
                <NetworkStatus />
                <Header />
                {children}
              </ConfigProvider>
            </AuthProvider>
          </ToastProvider>
        </ErrorBoundary>
      </body>
    </html>
  );
}
