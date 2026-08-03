"use client";

import { useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/auth";
import { dimensionLabel } from "@/lib/dimensions";

type EvidenceItem = {
  id: string;
  dimension: string;
  title: string;
  snippet: string;
  url: string;
  source_type: string;
  source_reliability?: string;  // WBS-20b: S/A/B/C/D/UNKNOWN
  meta_data: Record<string, unknown>;
  published_at: string | null;
  captured_at: string | null;
};

type Props = {
  taskId: string;
  filterId?: string | null;
  onFilterHandled?: () => void;
};

// WBS-20b: 来源可信等级样式
const RELIABILITY_STYLE: Record<string, string> = {
  S: "bg-emerald-100 text-emerald-700 border-emerald-200",
  A: "bg-green-100 text-green-700 border-green-200",
  B: "bg-blue-100 text-blue-700 border-blue-200",
  C: "bg-yellow-100 text-yellow-700 border-yellow-200",
  D: "bg-red-100 text-red-700 border-red-200",
};

function SourceReliabilityBadge({ level }: { level?: string }) {
  if (!level || level === "UNKNOWN") return null;
  const style = RELIABILITY_STYLE[level] || "bg-neutral-100 text-neutral-600 border-neutral-200";
  return (
    <span className={`ml-1 rounded-full border px-1.5 py-0.5 text-[10px] font-bold ${style}`}>
      {level}
    </span>
  );
}

export default function EvidencePanel({ taskId, filterId, onFilterHandled }: Props) {
  const [evidences, setEvidences] = useState<EvidenceItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filterDimension, setFilterDimension] = useState<string>("all");

  useEffect(() => {
    setIsLoading(true);
    authenticatedFetch(`/api/reports/${taskId}/evidences`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        const items = data.evidences || [];
        setEvidences(items);
        if (filterId && items.some((e: EvidenceItem) => e.id === filterId)) {
          setExpandedId(filterId);
          setFilterDimension("all");
          onFilterHandled?.();
        }
      })
      .catch(() => setEvidences([]))
      .finally(() => setIsLoading(false));
  }, [taskId, filterId]);

  const dimensions = Array.from(new Set(evidences.map((e) => e.dimension)));
  const filtered =
    filterDimension === "all"
      ? evidences
      : evidences.filter((e) => e.dimension === filterDimension);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="mr-3 h-5 w-5 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
        <span className="text-sm text-neutral-500">加载证据数据...</span>
      </div>
    );
  }

  if (evidences.length === 0) {
    return (
      <div className="py-12 text-center">
        <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-lg border border-neutral-950/10 bg-white text-xs font-semibold text-neutral-500">
          NIL
        </div>
        <p className="text-sm text-neutral-600">暂无证据记录</p>
        <p className="mt-1 text-xs text-neutral-400">
          任务可能使用传统模式执行，或尚未生成证据
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-medium text-neutral-950">
          证据索引
          <span className="ml-2 text-sm font-normal text-neutral-500">
            共 {evidences.length} 条
          </span>
        </h3>
      </div>

      {/* 维度筛选 */}
      {dimensions.length > 1 && (
        <div className="mb-4 flex flex-wrap gap-2">
          <button
            onClick={() => setFilterDimension("all")}
            className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
              filterDimension === "all"
                ? "bg-neutral-950 text-white"
                : "bg-neutral-950/5 text-neutral-600 hover:bg-neutral-950/10"
            }`}
          >
            全部
          </button>
          {dimensions.map((dim) => (
            <button
              key={dim}
              onClick={() => setFilterDimension(dim)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                filterDimension === dim
                  ? "bg-neutral-950 text-white"
                  : "bg-neutral-950/5 text-neutral-600 hover:bg-neutral-950/10"
              }`}
            >
              {dimensionLabel(dim)}
            </button>
          ))}
        </div>
      )}

      {/* 证据列表 */}
      <div className="max-h-[600px] space-y-3 overflow-y-auto">
        {filtered.map((ev) => {
          const isExpanded = expandedId === ev.id;
          return (
            <div
              key={ev.id}
              className="overflow-hidden rounded-lg border border-neutral-950/10 bg-white/70 transition-all hover:border-neutral-950/30"
            >
              <button
                onClick={() => setExpandedId(isExpanded ? null : ev.id)}
                className="flex w-full items-start gap-3 px-4 py-3 text-left"
              >
                <span className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full border border-neutral-950/10 bg-white text-xs text-neutral-500">
                  {isExpanded ? "−" : "+"}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="rounded-full border border-cyan-200 bg-cyan-50 px-2 py-0.5 text-xs font-medium text-cyan-700">
                      {dimensionLabel(ev.dimension)}
                    </span>
                    <SourceReliabilityBadge level={ev.source_reliability} />
                    <span className="text-xs text-neutral-400">{ev.source_type}</span>
                  </div>
                  <p className="truncate text-sm font-medium text-neutral-950">
                    {ev.title}
                  </p>
                  {!isExpanded && (
                    <p className="mt-1 truncate text-xs text-neutral-500">
                      {ev.snippet?.slice(0, 120)}
                      {ev.snippet?.length > 120 ? "..." : ""}
                    </p>
                  )}
                </div>
              </button>

              {isExpanded && (
                <div className="border-t border-neutral-950/10 px-4 pb-4 pt-0">
                  <p className="mt-3 text-sm leading-relaxed text-neutral-700">
                    {ev.snippet || "无摘要"}
                  </p>

                  {/* URL */}
                  {ev.url && (
                    <div className="mt-3">
                      <span className="text-xs text-neutral-500">来源：</span>
                      <a
                        href={ev.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="break-all text-xs text-neutral-950 underline underline-offset-4 hover:text-neutral-600"
                      >
                        {ev.url}
                      </a>
                    </div>
                  )}

                  {/* 元数据 */}
                  {ev.meta_data && Object.keys(ev.meta_data).length > 0 && (
                    <div className="mt-3 rounded-lg border border-neutral-950/10 bg-neutral-950/[0.03] p-3">
                      <p className="mb-1.5 text-xs font-medium text-neutral-500">
                        元数据
                      </p>
                      <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                        {Object.entries(ev.meta_data).map(([key, value]) => (
                          <div key={key} className="flex gap-1">
                            <dt className="flex-shrink-0 text-xs text-neutral-400">
                              {key}:
                            </dt>
                            <dd className="truncate text-xs text-neutral-700">
                              {String(value)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  )}

                  {/* 时间信息 */}
                  <div className="mt-3 flex gap-4 text-xs text-neutral-400">
                    {ev.published_at && <span>发布时间: {ev.published_at}</span>}
                    {ev.captured_at && <span>抓取时间: {ev.captured_at}</span>}
                  </div>

                  {/* 操作按钮 */}
                  <div className="mt-3 flex gap-2">
                    {ev.url && (
                      <a
                        href={ev.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-full border border-neutral-950/10 bg-white px-3 py-1.5 text-xs font-medium text-neutral-950 transition-colors hover:bg-neutral-950 hover:text-white"
                      >
                        访问原文 ↗
                      </a>
                    )}
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(ev.snippet).catch(() => {});
                      }}
                      className="rounded-full border border-neutral-950/10 bg-white px-3 py-1.5 text-xs font-medium text-neutral-600 transition-colors hover:bg-neutral-950/5"
                    >
                      复制摘要
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
