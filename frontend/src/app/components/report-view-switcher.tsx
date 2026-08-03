"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getReportBusinessView,
  type BusinessView,
  type BusinessViewType,
} from "@/lib/report-workspace";

type Props = {
  reportId: string;
  versionId: string;
  onEvidenceOpen: (evidenceId: string) => void;
  onError: (message: string) => void;
};

const VIEWS: Array<{ value: BusinessViewType; label: string; description: string }> = [
  { value: "EXECUTIVE_30S", label: "30 秒摘要", description: "快速判断是否值得继续" },
  { value: "ACCOUNT_BRIEF", label: "一页简报", description: "客户现状、风险与行动" },
  { value: "OPPORTUNITY_CARD", label: "商机卡", description: "Gate、反证和待验证项" },
  { value: "DEEP_REPORT", label: "深度报告", description: "完整正式报告" },
];

export function ReportViewSwitcher({ reportId, versionId, onEvidenceOpen, onError }: Props) {
  const [active, setActive] = useState<BusinessViewType>("EXECUTIVE_30S");
  const [view, setView] = useState<BusinessView | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getReportBusinessView(reportId, active)
      .then((result) => { if (!cancelled) setView(result); })
      .catch((error) => { if (!cancelled) onError(error instanceof Error ? error.message : "加载报告视图失败"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reportId, versionId, active]);

  return (
    <section>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {VIEWS.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => setActive(item.value)}
            className={`rounded-xl border p-3 text-left transition-colors ${
              active === item.value ? "border-neutral-950 bg-neutral-950 text-white" : "border-neutral-950/10 bg-white hover:border-neutral-950/30"
            }`}
          >
            <span className="block text-sm font-semibold">{item.label}</span>
            <span className={`mt-1 block text-xs ${active === item.value ? "text-neutral-300" : "text-neutral-500"}`}>{item.description}</span>
          </button>
        ))}
      </div>

      <div className="mt-5 rounded-xl border border-neutral-950/10 bg-white p-5 sm:p-7">
        {loading ? (
          <div className="py-12 text-center text-sm text-neutral-500">正在装配视图…</div>
        ) : view ? (
          <>
            <div className="mb-5 flex flex-wrap items-center justify-between gap-2 border-b border-neutral-950/10 pb-4">
              <div>
                <p className="text-xs font-semibold tracking-[0.14em] text-neutral-500">{view.view_type}</p>
                <h3 className="mt-1 text-lg font-semibold text-neutral-950">{view.title}</h3>
              </div>
              <span className="rounded-full bg-neutral-100 px-3 py-1 text-xs text-neutral-600">
                正式版本 V{view.version_no} · 报告引用证据 {view.citation_count} 条
              </span>
            </div>
            <div className="prose prose-neutral max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children, ...props }) => {
                    if (href?.startsWith("#evidence-")) {
                      const evidenceId = href.replace("#evidence-", "");
                      return (
                        <a
                          href={href}
                          onClick={(event) => { event.preventDefault(); onEvidenceOpen(evidenceId); }}
                          className="cursor-pointer text-neutral-950 underline underline-offset-4 hover:text-neutral-600"
                          {...props}
                        >{children}</a>
                      );
                    }
                    return <a href={href} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>;
                  },
                }}
              >
                {view.content_md.replace(/\[ev:([a-fA-F0-9-]{36})\]/g, "[ev:$1](#evidence-$1)")}
              </ReactMarkdown>
            </div>
          </>
        ) : (
          <p className="py-12 text-center text-sm text-neutral-500">当前视图不可用。</p>
        )}
      </div>
    </section>
  );
}
