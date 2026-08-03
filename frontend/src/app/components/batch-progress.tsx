"use client";

import { useMemo, useState } from "react";

type Props = {
  steps: { label: string; status: "done" | "current" | "pending" }[];
};

export function BatchProgress({ steps }: Props) {
  return (
    <div className="mb-8">
      <div className="flex items-center justify-center gap-1">
        {steps.map((step, i) => (
          <div key={step.label} className="flex items-center gap-1">
            {/* 步骤圆点 */}
            <div className="flex flex-col items-center">
              <div
                className={`flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold transition-colors ${
                  step.status === "done"
                    ? "bg-neutral-950 text-white"
                    : step.status === "current"
                    ? "border-2 border-neutral-950 bg-white text-neutral-950"
                    : "border border-neutral-200 bg-white text-neutral-300"
                }`}
              >
                {step.status === "done" ? "✓" : i + 1}
              </div>
              <span
                className={`mt-1.5 text-xs whitespace-nowrap ${
                  step.status === "current" ? "font-medium text-neutral-950" : "text-neutral-400"
                }`}
              >
                {step.label}
              </span>
            </div>
            {/* 连接线 */}
            {i < steps.length - 1 && (
              <div
                className={`mt-[-16px] h-0.5 w-6 sm:w-10 ${
                  step.status === "done" ? "bg-neutral-950" : "bg-neutral-200"
                }`}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export type BatchDiscoveryRow = {
  row_index: number;
  company_name: string | null;
  demand_direction: string | null;
  validation_status: string;
  error_message: string | null;
  task_id: string | null;
  candidate_ids: string[];
  target_account_id: string | null;
  target_status: string;
  research_status: string;
  signal_status: string;
  product_match_status: string;
  hypothesis_status: string;
};

const PAGE_SIZE = 25;

function StatePill({ value }: { value: string }) {
  const positive = ["CONFIRMED", "FOUND", "MATCHED", "COMPLETED", "CUSTOMER_VALIDATED", "SALES_ACCEPTED"];
  const blocked = ["NEEDS_DISAMBIGUATION", "ERROR", "FAILED", "BLOCKED", "REFUTED"];
  const className = positive.includes(value)
    ? "bg-emerald-100 text-emerald-800"
    : blocked.includes(value)
      ? "bg-red-100 text-red-800"
      : "bg-amber-100 text-amber-800";
  return <span className={`inline-flex rounded-full px-2 py-1 text-[11px] font-semibold ${className}`}>{value}</span>;
}

export function BatchExecutionProgress({
  rows,
  acceptedRows,
  rejectedRows,
}: {
  rows: BatchDiscoveryRow[];
  acceptedRows: number;
  rejectedRows: number;
}) {
  const [filter, setFilter] = useState("ALL");
  const [page, setPage] = useState(1);
  const filtered = useMemo(() => rows.filter((row) => {
    if (filter === "ALL") return true;
    if (filter === "REJECTED") return ["needs_disambiguation", "error"].includes(row.validation_status);
    if (filter === "RUNNING") return ["QUEUED", "RUNNING", "RECOVERING", "WAITING_FOR_INPUT"].includes(row.research_status);
    return row.research_status === filter;
  }), [filter, rows]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const visible = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <section data-testid="batch-discovery-progress" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">ROW PIPELINE</p>
          <h2 className="mt-1 text-lg font-semibold text-neutral-950">批量线索发现流水线</h2>
          <p className="mt-1 text-sm text-neutral-500">主体消歧、需求信号、研究、产品匹配和商机假设分别展示，拒绝行不会阻塞其他企业。</p>
        </div>
        <select
          value={filter}
          onChange={(event) => { setFilter(event.target.value); setPage(1); }}
          className="rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950"
          aria-label="筛选批次行"
        >
          <option value="ALL">全部行</option>
          <option value="REJECTED">待消歧 / 输入失败</option>
          <option value="RUNNING">执行中 / 待用户输入</option>
          <option value="COMPLETED">已完成</option>
          <option value="FAILED">研究失败</option>
        </select>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg bg-neutral-100 p-3"><p className="text-xs text-neutral-500">导入总行</p><strong className="mt-1 block text-xl text-neutral-950">{rows.length}</strong></div>
        <div className="rounded-lg bg-emerald-50 p-3"><p className="text-xs text-emerald-700">可执行行</p><strong className="mt-1 block text-xl text-emerald-950">{acceptedRows}</strong></div>
        <div className="rounded-lg bg-red-50 p-3"><p className="text-xs text-red-700">待修正行</p><strong className="mt-1 block text-xl text-red-950">{rejectedRows}</strong></div>
      </div>

      {visible.length === 0 ? (
        <p className="rounded-lg border border-dashed border-neutral-300 px-4 py-8 text-center text-sm text-neutral-500">当前筛选下没有导入行。</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-200">
          <table className="min-w-[980px] w-full text-left text-sm">
            <thead className="bg-neutral-50 text-xs text-neutral-500">
              <tr>
                <th className="px-3 py-3">企业 / 行</th><th className="px-3 py-3">主体</th><th className="px-3 py-3">需求信号</th><th className="px-3 py-3">研究</th><th className="px-3 py-3">产品匹配</th><th className="px-3 py-3">商机假设</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {visible.map((row) => (
                <tr key={row.row_index} data-testid={`batch-row-${row.row_index}`} className="align-top">
                  <td className="px-3 py-3">
                    <p className="font-medium text-neutral-950">{row.company_name || "未解析企业"}</p>
                    <p className="mt-1 text-xs text-neutral-500">第 {row.row_index + 1} 行 · {row.demand_direction || "未填写方向"}</p>
                    {row.error_message && <p className="mt-2 max-w-xs text-xs leading-5 text-red-700">{row.error_message}</p>}
                    {row.candidate_ids.length > 0 && <p className="mt-1 text-xs text-neutral-500">候选主体 {row.candidate_ids.length} 个，需人工选择</p>}
                  </td>
                  <td className="px-3 py-3"><StatePill value={row.target_status} /></td>
                  <td className="px-3 py-3"><StatePill value={row.signal_status} /></td>
                  <td className="px-3 py-3"><StatePill value={row.research_status} /></td>
                  <td className="px-3 py-3"><StatePill value={row.product_match_status} /></td>
                  <td className="px-3 py-3"><StatePill value={row.hypothesis_status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pageCount > 1 && (
        <div className="flex items-center justify-center gap-3 text-sm">
          <button className="rounded-full border border-neutral-300 px-3 py-1.5 disabled:opacity-40" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
          <span className="text-neutral-500">{page} / {pageCount}</span>
          <button className="rounded-full border border-neutral-300 px-3 py-1.5 disabled:opacity-40" disabled={page === pageCount} onClick={() => setPage((value) => value + 1)}>下一页</button>
        </div>
      )}
    </section>
  );
}
