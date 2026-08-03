"use client";

import type { DryRunSampleResult } from "@/lib/batch-import";

type Props = {
  samples: DryRunSampleResult[];
};

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function BatchDryRunResult({ samples }: Props) {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium text-neutral-700">
        采样执行结果（{samples.length} 个样本）
      </h3>

      <div className="space-y-3">
        {samples.map((s) => (
          <div
            key={s.row_index}
            className="rounded-lg border border-neutral-950/10 bg-white p-4"
          >
            {/* 头部 */}
            <div className="flex items-center justify-between mb-3">
              <div>
                <span className="text-sm font-medium text-neutral-900">{s.company_name}</span>
                <span className="mx-2 text-neutral-300">|</span>
                <span className="text-sm text-neutral-600">{s.demand_direction}</span>
              </div>
              <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                s.result && !s.result.error
                  ? "bg-green-100 text-green-700"
                  : "bg-red-100 text-red-700"
              }`}>
                {s.result && !s.result.error ? "✓ 完成" : "✗ 失败"}
              </span>
            </div>

            {/* 指标 */}
            {s.result && !s.result.error ? (
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div className="rounded bg-neutral-50 p-2 text-center">
                  <div className="text-neutral-500 mb-0.5">Token</div>
                  <div className="font-semibold text-neutral-800">
                    {formatTokens((s.result.tokens_used as number) || 0)}
                  </div>
                </div>
                <div className="rounded bg-neutral-50 p-2 text-center">
                  <div className="text-neutral-500 mb-0.5">耗时</div>
                  <div className="font-semibold text-neutral-800">
                    {(s.result.time_seconds as number)?.toFixed(1) || "—"}s
                  </div>
                </div>
                <div className="rounded bg-neutral-50 p-2 text-center">
                  <div className="text-neutral-500 mb-0.5">证据</div>
                  <div className="font-semibold text-neutral-800">
                    {s.result.evidence_count as number || 0} 条
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-xs text-red-600">
                {s.result?.error ? String(s.result.error) : "执行失败"}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
