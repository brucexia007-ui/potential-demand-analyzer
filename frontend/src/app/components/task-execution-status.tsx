import { StatusBadge } from "@/components/ui/workspace";
import type { ExecutionView } from "@/lib/task-execution";

export function TaskExecutionStatus({ execution }: { execution: ExecutionView | null }) {
  if (!execution) return null;
  const checkpoint = execution.latest_checkpoint;
  const observed = execution.observed_state;
  const message = observed === "PAUSING"
    ? "暂停请求已记录：等待当前外部调用结束后暂停，不会启动新的调用。"
    : observed === "RECOVERING"
      ? `正在从最近持久点恢复${checkpoint ? `：${checkpoint.dimension} / ${checkpoint.stage}` : ""}。`
      : observed === "PARTIAL"
        ? "任务已产出部分结果；请查看报告中的缺口和审计说明。"
        : observed === "WAITING_FOR_INPUT"
          ? "任务正在等待补充输入后继续执行。"
          : null;
  const currencyLabel = execution.budget.currencies.length
    ? ` ${execution.budget.currencies.join("/")}`
    : "";
  const budgetDetail = execution.budget.settlement_count > 0
    ? `已结算 ${execution.budget.settlement_count} 次调用 / ${execution.budget.settled_token_count.toLocaleString()} Token；金额 ${execution.budget.settled_amount.toFixed(6)}${currencyLabel}`
    : execution.budget.net_reserved_amount > 0
      ? `已预留 ${execution.budget.net_reserved_amount.toFixed(6)}${currencyLabel}`
      : "暂未记录外部调用";

  return (
    <div className="space-y-2 rounded-lg border border-neutral-950/10 bg-neutral-50 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={observed} label={observed} />
        {execution.recovery_count > 0 && <span className="text-neutral-600">已恢复 {execution.recovery_count} 次</span>}
        <span className="text-neutral-500">预算审计：{budgetDetail}</span>
      </div>
      {message && <p className="text-neutral-700">{message}</p>}
    </div>
  );
}
