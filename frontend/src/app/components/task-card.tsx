/**
 * 任务卡片组件
 */

"use client";

type TaskStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

type TaskCardProps = {
  task: {
    task_id: string;
    company_name: string;
    demand_direction: string;
    status: TaskStatus;
    created_at: string;
    has_report?: boolean;
  };
  onClick?: (taskId: string) => void;
};

// 获取状态标签样式
const getStatusStyle = (status: TaskStatus) => {
  switch (status) {
    case "COMPLETED":
      return "bg-green-100 text-green-700 border-green-200";
    case "FAILED":
      return "bg-red-100 text-red-700 border-red-200";
    case "RUNNING":
      return "bg-cyan-50 text-cyan-700 border-cyan-200";
    case "PENDING":
      return "bg-neutral-100 text-neutral-700 border-neutral-200";
    default:
      return "bg-neutral-100 text-neutral-700 border-neutral-200";
  }
};

// 获取状态中文显示
const getStatusText = (status: TaskStatus) => {
  switch (status) {
    case "COMPLETED":
      return "已完成";
    case "FAILED":
      return "已失败";
    case "RUNNING":
      return "执行中";
    case "PENDING":
      return "等待中";
    default:
      return status;
  }
};

// 格式化相对时间
const formatRelativeTime = (isoString: string) => {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return "刚刚";
    if (diffMins < 60) return `${diffMins}分钟前`;
    if (diffHours < 24) return `${diffHours}小时前`;
    if (diffDays < 7) return `${diffDays}天前`;
    return date.toLocaleDateString("zh-CN");
  } catch {
    return isoString;
  }
};

export default function TaskCard({ task, onClick }: TaskCardProps) {
  const statusStyle = getStatusStyle(task.status);
  const statusText = getStatusText(task.status);

  return (
    <div
      onClick={() => onClick?.(task.task_id)}
      className="group cursor-pointer rounded-lg border border-neutral-950/10 bg-white/80 p-4 shadow-[var(--shadow-panel)] transition-all hover:-translate-y-0.5 hover:border-neutral-950/30"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          {/* 公司名和需求方向 */}
          <div className="mb-2">
            <h3 className="truncate text-base font-medium text-neutral-950 transition-colors group-hover:text-neutral-700">
              {task.company_name}
            </h3>
            <p className="text-sm text-neutral-600 mt-1 truncate">
              {task.demand_direction}
            </p>
          </div>

          {/* 元信息 */}
          <div className="flex items-center gap-4 text-xs text-neutral-500">
            <span className="flex items-center gap-1">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              {formatRelativeTime(task.created_at)}
            </span>
            {task.has_report && (
              <span className="flex items-center gap-1 text-green-600">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                有报告
              </span>
            )}
          </div>
        </div>

        {/* 状态标签 */}
        <span className={`whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium ${statusStyle}`}>
          {statusText}
        </span>
      </div>
    </div>
  );
}
