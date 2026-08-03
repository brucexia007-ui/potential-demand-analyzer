/**
 * 历史任务列表页面
 */

"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell, SegmentedControl, StatusBadge } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

type TaskStatus =
  | "PENDING"
  | "QUEUED"
  | "RUNNING"
  | "PAUSED"
  | "WAITING_FOR_INPUT"
  | "COMPLETED"
  | "PARTIAL"
  | "FAILED"
  | "CANCELLED";
type TaskFilter = "ALL" | TaskStatus;

type Task = {
  task_id: string;
  company_name: string;
  demand_direction: string;
  status: TaskStatus;
  created_at: string;
  has_report?: boolean;
};

type TaskListResponse = {
  total: number;
  page: number;
  page_size: number;
  tasks: Task[];
};

const STATUS_TABS: { key: TaskFilter; label: string }[] = [
  { key: "ALL", label: "全部" },
  { key: "RUNNING", label: "执行中" },
  { key: "COMPLETED", label: "已完成" },
  { key: "PARTIAL", label: "部分完成" },
  { key: "FAILED", label: "已失败" },
  { key: "PAUSED", label: "已暂停" },
  { key: "CANCELLED", label: "已取消" },
  { key: "PENDING", label: "等待中" },
];

export default function HistoryPage() {
  const router = useRouter();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedStatus, setSelectedStatus] = useState<TaskFilter>("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [totalTasks, setTotalTasks] = useState(0);
  const [searchInput, setSearchInput] = useState("");

  const pageSize = 20;

  const { error: toastError } = useToast();

  // 搜索防抖
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearchQuery(searchInput);
      setCurrentPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // 获取任务列表
  useEffect(() => {
    const fetchTasks = async () => {
      setIsLoading(true);
      try {
        const params = new URLSearchParams();
        if (selectedStatus !== "ALL") params.set("status", selectedStatus);
        if (searchQuery) params.set("search", searchQuery);
        params.set("page", currentPage.toString());
        params.set("page_size", pageSize.toString());

        const res = await authenticatedFetch(`/api/tasks?${params.toString()}`);
        if (!res.ok) {
          throw new Error(`加载失败 (${res.status})`);
        }
        const data: TaskListResponse = await res.json();
        setTasks(data.tasks);
        setTotalTasks(data.total);
        setTotalPages(Math.ceil(data.total / data.page_size));
      } catch (err) {
        toastError(err instanceof Error ? err.message : "加载失败");
      } finally {
        setIsLoading(false);
      }
    };

    fetchTasks();
  }, [selectedStatus, searchQuery, currentPage]);

  // 处理任务点击
  const handleTaskClick = (taskId: string) => {
    router.push(`/tasks/${taskId}`);
  };

  // 清空搜索
  const handleClearSearch = () => {
    setSearchInput("");
    setSearchQuery("");
    setCurrentPage(1);
  };

  // 格式化日期
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const getStatusLabel = (status: TaskStatus) => {
    const labels: Record<TaskStatus, string> = {
      RUNNING: "执行中",
      QUEUED: "排队中",
      COMPLETED: "已完成",
      PARTIAL: "部分完成",
      FAILED: "已失败",
      PAUSED: "已暂停",
      WAITING_FOR_INPUT: "等待确认",
      CANCELLED: "已取消",
      PENDING: "等待中",
    };
    return labels[status];
  };

  return (
    <PageShell>
      <PageHeader
        eyebrow="TASK ARCHIVE"
        title="历史任务"
        description={`共 ${totalTasks} 个任务`}
        action={
          <Button variant="primary" onClick={() => router.push("/")}>
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            新建任务
          </Button>
        }
      />

        {/* 筛选器 */}
        <Card variant="bordered" padding="md" className="mb-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-center">
            {/* 状态筛选 */}
            <SegmentedControl
              options={STATUS_TABS.map((tab) => ({ value: tab.key, label: tab.label }))}
              value={selectedStatus}
              onChange={(value) => setSelectedStatus(value as TaskFilter)}
            />

            {/* 搜索框 */}
            <div className="relative flex-1 md:ml-auto">
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="搜索公司名或需求方向..."
                className="w-full rounded-full border border-neutral-950/10 bg-white px-4 py-2 pl-10 text-sm focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10 md:w-72"
              />
              <svg
                className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
              {searchInput && (
                <button
                  onClick={handleClearSearch}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </Card>

        {/* 任务列表 */}
        <section className="space-y-3">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <div className="mr-3 h-6 w-6 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent"></div>
              <span className="text-neutral-600">加载任务列表中...</span>
            </div>
          ) : tasks.length === 0 ? (
            <Card variant="bordered" padding="lg" className="text-center py-16">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-lg border border-neutral-950/10 bg-white">
                <svg className="h-8 w-8 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
              </div>
              <h3 className="mb-2 text-lg font-medium text-neutral-950">暂无任务</h3>
              <p className="text-neutral-600 mb-6">
                {searchQuery || selectedStatus !== "ALL"
                  ? "没有符合条件的任务"
                  : "还没有创建任何任务，开始第一个分析任务吧！"}
              </p>
              {!searchQuery && selectedStatus === "ALL" && (
                <Button variant="primary" onClick={() => router.push("/")}>
                  创建第一个任务
                </Button>
              )}
            </Card>
          ) : (
            <>
              {tasks.map((task) => (
                <div
                  key={task.task_id}
                  onClick={() => handleTaskClick(task.task_id)}
                  className="group cursor-pointer rounded-lg border border-neutral-950/10 bg-white/80 p-4 shadow-[var(--shadow-panel)] transition-all hover:-translate-y-0.5 hover:border-neutral-950/30"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 mb-2">
                        <StatusBadge status={task.status} label={getStatusLabel(task.status)} />
                        <span className="text-xs text-neutral-500">
                          {formatDate(task.created_at)}
                        </span>
                        {(task.status === "COMPLETED" ||
                          task.status === "PARTIAL" ||
                          task.status === "FAILED") && (
                          <span className={`text-xs font-medium flex items-center gap-1 ${
                            task.has_report ? "text-green-600" : "text-neutral-400"
                          }`}>
                            {task.has_report ? (
                              <>
                                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                </svg>
                                有报告
                              </>
                            ) : (
                              <>
                                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                </svg>
                                无报告
                              </>
                            )}
                          </span>
                        )}
                      </div>
                      <h3 className="mb-1 text-base font-medium text-neutral-950 transition-colors group-hover:text-neutral-700">
                        {task.company_name}
                      </h3>
                      <p className="text-sm text-neutral-600">
                        {task.demand_direction}
                      </p>
                    </div>
                    <svg className="mt-1 h-5 w-5 flex-shrink-0 text-neutral-400 transition-transform group-hover:translate-x-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </div>
                </div>
              ))}

              {/* 分页 */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between pt-4">
                  <p className="text-sm text-neutral-600">
                    第 {currentPage} 页，共 {totalPages} 页
                  </p>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                      disabled={currentPage === 1}
                    >
                      上一页
                    </Button>
                    <span className="min-w-[40px] rounded-full bg-neutral-950 px-3 py-1.5 text-center text-sm font-medium text-white">
                      {currentPage}
                    </span>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                      disabled={currentPage === totalPages}
                    >
                      下一页
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </section>
    </PageShell>
  );
}
