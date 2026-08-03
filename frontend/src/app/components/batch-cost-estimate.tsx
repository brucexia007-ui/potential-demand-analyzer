"use client";

import type { CostEstimate as CostEstimateType } from "@/lib/batch-import";

type Props = {
  estimate: CostEstimateType;
};

export function BatchCostEstimate({ estimate }: Props) {
  const confidenceColor =
    estimate.confidence === "high"
      ? "text-green-700 bg-green-100"
      : estimate.confidence === "medium"
      ? "text-yellow-700 bg-yellow-100"
      : "text-red-700 bg-red-100";

  const confidenceLabel =
    estimate.confidence === "high" ? "高" : estimate.confidence === "medium" ? "中" : "低";

  return (
    <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-4 space-y-3">
      <h4 className="text-sm font-medium text-yellow-800">资源预算估算（规划外推，仅供参考）</h4>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <div>
          <span className="text-yellow-700">预估 Token：</span>
          <span className="font-medium text-yellow-900">
            {estimate.estimated_total_tokens.toLocaleString()}
          </span>
        </div>
        <div>
          <span className="text-yellow-700">预估耗时：</span>
          <span className="font-medium text-yellow-900">
            {estimate.estimated_total_time_minutes.toFixed(0)} 分钟
          </span>
        </div>
        <div>
          <span className="text-yellow-700">金额费用：</span>
          <span className="font-medium text-yellow-900">
            暂不可估算
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-yellow-700">置信度：</span>
          <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${confidenceColor}`}>
            {confidenceLabel}
          </span>
        </div>
      </div>

      {/* 比例说明 */}
      <div className="text-xs text-yellow-600 space-y-0.5">
        <p>· 基于 {estimate.sample_count} 个采样条目的 Skill 声明预算，不调用外部 Provider</p>
        <p>· 假设 {estimate.total_rows} 条全部执行，按采样平均量外推</p>
        <p>· {estimate.monetary_cost.reason}</p>
        <p>· {estimate.estimate_basis}</p>
        {estimate.confidence === "low" && (
          <p>⚠ 采样结果差异较大，实际消耗可能与估算有显著偏差</p>
        )}
      </div>
    </div>
  );
}
