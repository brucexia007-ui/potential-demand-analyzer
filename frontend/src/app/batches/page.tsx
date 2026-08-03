"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PageHeader, PageShell, SegmentedControl, StatusBadge } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

type BatchItem = {
  batch_id: string;
  name: string;
  status: string;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  created_at: string;
};

type BatchListData = {
  total: number;
  page: number;
  page_size: number;
  batches: BatchItem[];
};

const STATUS_OPTIONS = [
  { value: "", label: "全部" },
  { value: "RUNNING", label: "执行中" },
  { value: "COMPLETED", label: "已完成" },
  { value: "FAILED", label: "已失败" },
  { value: "CANCELLED", label: "已取消" },
  { value: "PARTIAL", label: "部分完成" },
];

function statusLabel(s: string): string {
  const opt = STATUS_OPTIONS.find((o) => o.value === s);
  return opt?.label ?? s;
}

export default function BatchesPage() {
  const router = useRouter();
  const { user, isLoading, authState } = useAuth();

  useEffect(() => {
    if (authState === "unauthenticated") {
      router.push("/login?redirect=/batches");
    }
  }, [authState, router]);

  const [data, setData] = useState<BatchListData | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    params.set("page", String(page));
    params.set("page_size", "20");
    if (search) params.set("search", search);

    authenticatedFetch(`/api/batches?${params.toString()}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setData(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [user, status, page, search]);

  // 搜索防抖
  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const formatTime = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  if (isLoading || authState === "unavailable" || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-neutral-500">加载中...</p>
      </main>
    );
  }

  return (
    <PageShell>
      <PageHeader
        eyebrow="BATCH CONTROL"
        title="批量任务"
        description={`共 ${data?.total ?? 0} 个批次`}
        action={
          <Button variant="primary" onClick={() => router.push("/batches/new")}>
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            新建批量任务
          </Button>
        }
      />

        {/* 筛选 */}
        <Card variant="bordered" padding="md">
          <div className="flex flex-wrap items-center gap-4">
            <SegmentedControl
              options={STATUS_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
              value={status}
              onChange={(value) => { setStatus(value); setPage(1); }}
            />
            <div className="min-w-[200px] flex-1">
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="搜索批次名称..."
                className="w-full rounded-full border border-neutral-950/10 bg-white px-4 py-2 text-sm focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10"
              />
            </div>
          </div>
        </Card>

        {/* 批次列表 */}
        <div className="mt-6 space-y-4">
          {loading ? (
            <p className="py-12 text-center text-neutral-500">加载中...</p>
          ) : data && data.batches.length === 0 ? (
            <p className="py-12 text-center text-neutral-500">暂无批次</p>
          ) : (
            data?.batches.map((batch) => (
              <Card
                key={batch.batch_id}
                variant="bordered"
                padding="md"
              >
                <div
                  className="group cursor-pointer"
                  onClick={() => router.push(`/batches/${batch.batch_id}`)}
                >
                  <div className="mb-3 flex items-center justify-between gap-4">
                    <h3 className="font-medium text-neutral-950 transition-colors group-hover:text-neutral-700">
                      {batch.name}
                    </h3>
                    <StatusBadge status={batch.status} label={statusLabel(batch.status)} />
                  </div>

                  {/* 进度条 */}
                  <div className="mb-2 flex h-2 w-full overflow-hidden rounded-full bg-neutral-950/10">
                    {batch.total_tasks > 0 && (
                      <>
                        <span
                          className="h-full bg-green-500"
                          style={{ width: `${(batch.completed_tasks / batch.total_tasks) * 100}%` }}
                        />
                        <span
                          className="h-full bg-red-400"
                          style={{ width: `${(batch.failed_tasks / batch.total_tasks) * 100}%` }}
                        />
                      </>
                    )}
                  </div>

                  <div className="flex items-center justify-between text-xs text-neutral-500">
                    <span>
                      {batch.completed_tasks} / {batch.total_tasks} 完成
                      {batch.failed_tasks > 0 && (
                        <span className="text-red-500 ml-2">{batch.failed_tasks} 失败</span>
                      )}
                    </span>
                    <span>{formatTime(batch.created_at)}</span>
                  </div>
                </div>
              </Card>
            ))
          )}

          {/* 分页 */}
          {data && data.total > data.page_size && (
            <div className="flex items-center justify-center gap-4 pt-4">
              <Button
                variant="secondary"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </Button>
              <span className="text-sm text-neutral-500">
                {page} / {Math.ceil(data.total / data.page_size)}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= Math.ceil(data.total / data.page_size)}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          )}
        </div>
    </PageShell>
  );
}
