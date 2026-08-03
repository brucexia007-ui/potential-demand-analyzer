"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { OpportunityFunnel } from "@/app/components/opportunity-funnel";
import { useAuth } from "@/components/providers/auth-provider";


export default function DashboardPage() {
  const router = useRouter();
  const { authState } = useAuth();

  useEffect(() => {
    if (authState === "unauthenticated") {
      router.push("/login?redirect=/dashboard");
    }
  }, [authState, router]);

  if (authState !== "authenticated") {
    return <main className="mx-auto max-w-7xl px-4 py-16 text-center text-sm text-neutral-500">正在验证工作区权限…</main>;
  }

  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-7 max-w-3xl">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-neutral-500">BUSINESS OUTCOMES</p>
        <h1 className="mt-2 text-4xl font-semibold leading-tight text-neutral-950 sm:text-5xl">商机经营仪表盘</h1>
        <p className="mt-3 text-base leading-7 text-neutral-600">从研究客户、OIG 过门、销售接受和客户验证，一直追踪到正式商机与成交；所有金额、成本和反馈均来自可审计事实账本。</p>
      </header>
      <OpportunityFunnel />
    </main>
  );
}
