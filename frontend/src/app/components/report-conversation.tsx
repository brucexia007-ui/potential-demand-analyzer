"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ReportDraftReview } from "./report-draft-review";
import { FollowUpResearchStatus } from "./follow-up-research-status";
import {
  askReportQuestion,
  createReportThread,
  listReportMessages,
  listReportThreads,
  listThreadFollowUps,
  previewFollowUpResearch,
  startFollowUpResearch,
  type ReportIntent,
  type ReportMessage,
  type ReportThread,
  type FollowUpResearchSummary,
} from "@/lib/report-workspace";

type Props = {
  reportId: string;
  onReportAccepted: () => Promise<void> | void;
  onError: (message: string) => void;
};

const INTENTS: Array<{ value: ReportIntent; label: string; description: string }> = [
  { value: "EXPLANATION", label: "解释报告", description: "只基于现有证据回答" },
  { value: "FOLLOW_UP_RESEARCH", label: "补充研究", description: "新建可追踪研究任务" },
  { value: "REPORT_REVISION", label: "形成修订建议", description: "记录为报告修订意图" },
];

export function ReportConversation({ reportId, onReportAccepted, onError }: Props) {
  const [threads, setThreads] = useState<ReportThread[]>([]);
  const [activeThread, setActiveThread] = useState<ReportThread | null>(null);
  const [messages, setMessages] = useState<ReportMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [intent, setIntent] = useState<ReportIntent>("EXPLANATION");
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [followUps, setFollowUps] = useState<FollowUpResearchSummary[]>([]);
  const [draftRefreshToken, setDraftRefreshToken] = useState(0);

  const loadMessages = async (thread: ReportThread) => {
    setMessages(await listReportMessages(thread.id));
  };

  const loadFollowUps = async (thread: ReportThread) => {
    setFollowUps(await listThreadFollowUps(thread.id));
  };

  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      try {
        let items = await listReportThreads(reportId);
        if (items.length === 0) {
          items = [await createReportThread(reportId, "报告深度讨论")];
        }
        if (cancelled) return;
        setThreads(items);
        setActiveThread(items[0]);
        const [threadMessages, threadFollowUps] = await Promise.all([
          listReportMessages(items[0].id),
          listThreadFollowUps(items[0].id),
        ]);
        setMessages(threadMessages);
        setFollowUps(threadFollowUps);
      } catch (error) {
        if (!cancelled) onError(error instanceof Error ? error.message : "初始化报告讨论失败");
      }
    };
    void initialize();
    return () => { cancelled = true; };
  }, [reportId]);

  const submit = async () => {
    const normalized = question.trim();
    if (!activeThread || !normalized || submitting) return;
    setSubmitting(true);
    setNotice(null);
    try {
      if (intent === "FOLLOW_UP_RESEARCH") {
        const idempotencyKey = crypto.randomUUID();
        const preview = await previewFollowUpResearch(activeThread.id, normalized, idempotencyKey);
        let confirmed = !preview.requires_confirmation;
        if (preview.requires_confirmation) {
          confirmed = window.confirm(
            `该补充研究预计至少 ${preview.estimated_external_call_lower_bound} 次外部调用、约 ${preview.estimated_total_tokens.toLocaleString()} Token。\n\n${preview.confirmation_reasons.join("\n")}\n\n是否继续？`,
          );
        }
        if (!confirmed) return;
        const started = await startFollowUpResearch(
          activeThread.id,
          normalized,
          idempotencyKey,
          preview.requires_confirmation,
        );
        setNotice(started.status === "STARTED"
          ? `补充研究已启动，子任务 ${started.task_id ?? "正在创建"}。原报告不会被静默修改。`
          : "补充研究仍需成本确认。"
        );
        if (started.status === "STARTED") await loadFollowUps(activeThread);
      } else {
        const result = await askReportQuestion(activeThread.id, normalized, intent);
        if (result.status === "CONTEXT_ACTION_REQUIRED") {
          setNotice(`当前问题需要先压缩或拆分上下文：${result.context_reasons.join("；")}`);
        } else if (result.status === "DRAFT_CREATED") {
          setNotice("修订草案已生成。请在下方逐项审阅 Diff；正式报告尚未改变。");
        } else if (result.status === "ROUTED") {
          setNotice(intent === "REPORT_REVISION"
            ? "修订意图已记录。正式报告只会通过草案、Diff 和确认生成新版本。"
            : "请求已路由到对应工作流。"
          );
        } else if (result.status === "NEEDS_INTENT_SELECTION") {
          setNotice("请明确选择解释报告、补充研究或形成修订建议。"
          );
        } else {
          setNotice(`回答已生成，并绑定 ${result.citation_count} 条上下文来源。`);
        }
      }
      setQuestion("");
      await loadMessages(activeThread);
    } catch (error) {
      onError(error instanceof Error ? error.message : "报告讨论请求失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card variant="bordered" padding="md" className="mt-8 bg-neutral-50/90">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-[0.16em] text-neutral-500">REPORT AGENT</p>
          <h3 className="mt-1 text-lg font-semibold text-neutral-950">继续与报告智能体探讨</h3>
          <p className="mt-1 text-sm text-neutral-600">会话绑定当前不可变报告版本；补充研究与修订不会覆盖原报告。</p>
        </div>
        {threads.length > 1 && (
          <select
            value={activeThread?.id ?? ""}
            onChange={(event) => {
              const thread = threads.find((item) => item.id === event.target.value);
              if (thread) {
                setActiveThread(thread);
                void Promise.all([loadMessages(thread), loadFollowUps(thread)]);
              }
            }}
            className="rounded-lg border border-neutral-950/20 bg-white px-3 py-2 text-sm"
          >
            {threads.map((thread) => <option key={thread.id} value={thread.id}>{thread.title}</option>)}
          </select>
        )}
      </div>

      <div className="mt-5 max-h-96 space-y-3 overflow-y-auto rounded-xl border border-neutral-950/10 bg-white p-4">
        {messages.length === 0 ? (
          <p className="py-8 text-center text-sm text-neutral-500">可以追问结论依据、要求补证，或提出报告修订方向。</p>
        ) : messages.map((message) => (
          <div key={message.id} className={`flex ${message.role === "USER" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-6 ${
              message.role === "USER" ? "bg-neutral-950 text-white" : "bg-neutral-100 text-neutral-900"
            }`}>
              <p className="whitespace-pre-wrap">{message.content}</p>
              <p className={`mt-1 text-[11px] ${message.role === "USER" ? "text-neutral-300" : "text-neutral-500"}`}>
                {message.intent} · {new Date(message.created_at).toLocaleString("zh-CN")}
              </p>
            </div>
          </div>
        ))}
      </div>

      {notice && <p className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>}

      {followUps[0] && (
        <FollowUpResearchStatus
          key={followUps[0].research_run_id}
          initialSummary={followUps[0]}
          onError={onError}
          onDraftCreated={() => setDraftRefreshToken((current) => current + 1)}
          onSummaryChange={(next) => {
            setFollowUps((current) => current.map((item) => (
              item.research_run_id === next.research_run_id ? next : item
            )));
          }}
        />
      )}

      <div className="mt-4 grid gap-2 md:grid-cols-3">
        {INTENTS.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => setIntent(item.value)}
            className={`rounded-xl border p-3 text-left ${
              intent === item.value ? "border-neutral-950 bg-white" : "border-neutral-950/10 bg-white/60"
            }`}
          >
            <span className="block text-sm font-semibold text-neutral-950">{item.label}</span>
            <span className="mt-1 block text-xs text-neutral-500">{item.description}</span>
          </button>
        ))}
      </div>

      <textarea
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        maxLength={6000}
        rows={4}
        placeholder="例如：这项商机判断最关键的反向证据是什么？请补充研究合同到期与现供应商锁定情况。"
        className="mt-4 w-full rounded-xl border border-neutral-950/20 bg-white px-4 py-3 text-sm outline-none focus:border-neutral-950 focus:ring-2 focus:ring-neutral-950/10"
      />
      <div className="mt-3 flex justify-end">
        <Button type="button" disabled={!activeThread || !question.trim()} isLoading={submitting} onClick={() => void submit()}>
          {intent === "FOLLOW_UP_RESEARCH" ? "预览并启动补充研究" : "发送给报告智能体"}
        </Button>
      </div>

      <ReportDraftReview
        key={`${reportId}:${messages.length}:${draftRefreshToken}`}
        reportId={reportId}
        onAccepted={onReportAccepted}
        onError={onError}
      />
    </Card>
  );
}
