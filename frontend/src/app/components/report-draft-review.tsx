"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  decideReportDraft,
  listReportDrafts,
  type ReportDraft,
  type ReportDraftChange,
} from "@/lib/report-workspace";

type Props = {
  reportId: string;
  onAccepted: () => Promise<void> | void;
  onError: (message: string) => void;
};

const KIND_LABELS: Record<ReportDraftChange["kind"], string> = {
  INSERT: "新增",
  DELETE: "删除",
  REPLACE: "替换",
};

export function ReportDraftReview({ reportId, onAccepted, onError }: Props) {
  const [drafts, setDrafts] = useState<ReportDraft[]>([]);
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [decidingId, setDecidingId] = useState<string | null>(null);

  const refresh = async () => {
    const items = await listReportDrafts(reportId);
    setDrafts(items);
    setSelected((current) => {
      const next = { ...current };
      for (const draft of items) {
        if (!(draft.id in next)) next[draft.id] = [];
      }
      return next;
    });
  };

  useEffect(() => {
    void refresh().catch((error) => onError(error instanceof Error ? error.message : "加载报告草案失败"));
  }, [reportId]);

  const decide = async (
    draft: ReportDraft,
    action: "ACCEPT_ALL" | "ACCEPT_SELECTED" | "REJECT",
  ) => {
    if (decidingId) return;
    if (action === "REJECT" && !window.confirm("确认拒绝这份报告修订草案？原正式版本不会改变。")) return;
    setDecidingId(draft.id);
    try {
      const result = await decideReportDraft(draft.id, action, selected[draft.id] ?? []);
      await refresh();
      if (result.status === "ACCEPTED" || result.status === "PARTIALLY_ACCEPTED") await onAccepted();
    } catch (error) {
      onError(error instanceof Error ? error.message : "报告草案裁决失败");
      await refresh().catch(() => undefined);
    } finally {
      setDecidingId(null);
    }
  };

  if (drafts.length === 0) return null;

  return (
    <section className="mt-5 space-y-4 border-t border-neutral-950/10 pt-5">
      <div>
        <p className="text-xs font-semibold tracking-[0.16em] text-neutral-500">REVISION DRAFTS</p>
        <h4 className="mt-1 text-base font-semibold text-neutral-950">报告修订草案与 Diff</h4>
        <p className="mt-1 text-sm text-neutral-600">智能体只能提出草案；接受后才会创建新的不可变正式版本。</p>
      </div>

      {drafts.map((draft) => {
        const selectedIds = selected[draft.id] ?? [];
        const pending = draft.status === "DRAFT";
        const followUpManifest = draft.proposed_evidence_index.follow_up_runs;
        const hasAssetChanges = Array.isArray(followUpManifest) && followUpManifest.length > 0;
        return (
          <article key={draft.id} className="rounded-xl border border-neutral-950/10 bg-white p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="font-medium text-neutral-950">{draft.summary}</p>
                <p className="mt-1 text-xs text-neutral-500">
                  {new Date(draft.created_at).toLocaleString("zh-CN")} · {draft.change_set.length} 项变更
                </p>
              </div>
              <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                pending ? "bg-amber-100 text-amber-800" : "bg-neutral-100 text-neutral-600"
              }`}>{draft.status}</span>
            </div>

            <div className="mt-4 space-y-3">
              {draft.change_set.map((change) => {
                const checked = selectedIds.includes(change.id);
                return (
                  <label key={change.id} className="block rounded-lg border border-neutral-950/10 p-3">
                    <div className="flex items-center gap-2">
                      {pending && !hasAssetChanges && (
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => setSelected((current) => ({
                            ...current,
                            [draft.id]: checked
                              ? selectedIds.filter((id) => id !== change.id)
                              : [...selectedIds, change.id],
                          }))}
                          className="h-4 w-4 accent-neutral-950"
                        />
                      )}
                      <span className="text-xs font-semibold text-neutral-700">
                        {KIND_LABELS[change.kind]} · {change.id}
                      </span>
                    </div>
                    {change.before && (
                      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-red-50 p-2 text-xs leading-5 text-red-800">- {change.before}</pre>
                    )}
                    {change.after && (
                      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-emerald-50 p-2 text-xs leading-5 text-emerald-800">+ {change.after}</pre>
                    )}
                  </label>
                );
              })}
            </div>

            {pending && hasAssetChanges && (
              <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                该草案同时变更正文、原始数据和 Evidence 索引。为保证引用完整，只能整体接受或拒绝。
              </p>
            )}

            {pending && (
              <div className="mt-4 flex flex-wrap justify-end gap-2">
                <Button size="sm" variant="ghost" disabled={decidingId === draft.id} onClick={() => void decide(draft, "REJECT")}>拒绝</Button>
                {!hasAssetChanges && (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={selectedIds.length === 0}
                    isLoading={decidingId === draft.id}
                    onClick={() => void decide(draft, "ACCEPT_SELECTED")}
                  >接受所选（{selectedIds.length}）</Button>
                )}
                <Button size="sm" isLoading={decidingId === draft.id} onClick={() => void decide(draft, "ACCEPT_ALL")}>全部接受</Button>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
