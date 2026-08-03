"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/providers/auth-provider";
import { useConfig } from "@/components/providers/config-provider";
import { SmartTaskForm } from "@/app/components/smart-task-form";

function useTypewriter(text: string, speed = 32, startDelay = 420) {
  const [displayed, setDisplayed] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    setDisplayed("");
    setDone(false);
    let index = 0;
    let interval: ReturnType<typeof setInterval> | undefined;

    const timeout = setTimeout(() => {
      interval = setInterval(() => {
        index += 1;
        setDisplayed(text.slice(0, index));
        if (index >= text.length) {
          setDone(true);
          if (interval) clearInterval(interval);
        }
      }, speed);
    }, startDelay);

    return () => {
      clearTimeout(timeout);
      if (interval) clearInterval(interval);
    };
  }, [text, speed, startDelay]);

  return { displayed, done };
}

export default function HomePage() {
  const router = useRouter();
  const { authState } = useAuth();
  const { status: configStatus, isLoading: configLoading } = useConfig();

  useEffect(() => {
    if (authState === "unauthenticated") {
      router.push("/login?redirect=/");
    }
  }, [authState, router]);

  const [actionsVisible, setActionsVisible] = useState(false);
  const executionMessage = configLoading || !configStatus
    ? "正在确认系统执行状态，请稍候。"
    : configStatus.execution_ready
      ? "情报链路已就绪。输入客户需求，系统自动解析意图并生成可回溯的调研计划。"
      : "系统执行能力尚未就绪。当前可浏览客户、产品、Skill 与历史资产，完成配置后可发起研究。";
  const intro = useTypewriter(executionMessage);

  useEffect(() => {
    const timer = setTimeout(() => setActionsVisible(true), 400);
    return () => clearTimeout(timer);
  }, []);

  const quickActions = [
    { label: "批量导入", onClick: () => router.push("/batches/new") },
    { label: "查看历史", onClick: () => router.push("/history") },
    { label: "配置 Provider", onClick: () => router.push("/settings/providers") },
  ];

  return (
    <main className="relative min-h-screen overflow-hidden pb-12">
      <section className="mx-auto grid w-full max-w-7xl gap-6 px-4 pt-8 sm:px-6 sm:pt-10 lg:grid-cols-[minmax(0,1fr)_500px] lg:px-8">
        <div className="flex min-h-[560px] flex-col justify-between rounded-lg border border-neutral-950/10 bg-white/60 p-5 shadow-[var(--shadow-panel)] backdrop-blur-sm sm:p-8">
          <div>
            <p className="pointer-events-none mb-6 select-none text-[clamp(18px,3vw,26px)] leading-snug text-neutral-950 blur-[3px]">
              指挥台联机完成
              <br />
              Adaptive Demand Intelligence Console
            </p>
            <h1 className="max-w-3xl text-[clamp(40px,8vw,92px)] font-semibold leading-[0.95] text-neutral-950">
              创建分析任务
            </h1>
            <p className="mt-6 min-h-[74px] max-w-2xl text-[clamp(18px,3vw,26px)] leading-snug text-neutral-800">
              {intro.displayed}
              {!intro.done && <span className="type-cursor" />}
            </p>
          </div>

          <div
            className={`mt-8 flex flex-wrap gap-2 transition-all duration-500 ${
              actionsVisible ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0"
            }`}
          >
            {quickActions.map((action) => (
              <button
                key={action.label}
                type="button"
                onClick={action.onClick}
                className="inline-flex items-center justify-center rounded-full border border-neutral-950/10 bg-white px-4 py-2 text-sm font-medium text-neutral-950 transition-colors hover:bg-neutral-950 hover:text-white sm:px-5"
              >
                {action.label}
              </button>
            ))}
          </div>
        </div>

        <div id="task-form" className="scroll-mt-24">
          <SmartTaskForm />
        </div>
      </section>

      <section className="mx-auto mt-6 grid w-full max-w-7xl grid-cols-1 gap-3 px-4 sm:px-6 md:grid-cols-3 lg:px-8">
        {[
          ["智能评估", "自动评估搜索质量，不达标时反思优化"],
          ["自我纠偏", "多轮迭代优化，持续提升结果质量"],
                ["预算审计", "实时记录 Token 与费用，达到阈值告警但不中断质量步骤"],
        ].map(([title, desc]) => (
          <div key={title} className="rounded-lg border border-neutral-950/10 bg-white/75 p-5 shadow-[var(--shadow-panel)]">
            <div className="mb-3 h-1.5 w-8 rounded-full bg-[var(--signal-lime)]" />
            <div className="mb-1 text-sm font-semibold text-neutral-950">{title}</div>
            <p className="text-sm leading-6 text-neutral-600">{desc}</p>
          </div>
        ))}
      </section>
    </main>
  );
}
