"use client";

import { useEffect, useState } from "react";
import {
  confirmSkillImport,
  fetchSkillImportJob,
  mockSkillImport,
  previewGitHubSkillImport,
  previewOfflineSkillImport,
  type SkillImportConfirmation,
  type SkillImportJob,
  type SkillImportMock,
} from "@/lib/skills";

type Props = {
  onClose: () => void;
  onImported: (result: SkillImportConfirmation) => void | Promise<void>;
};

const STEPS = ["来源", "风险", "转换", "Diff", "Mock", "确认"] as const;

export function SkillImportWizard({ onClose, onImported }: Props) {
  const [step, setStep] = useState(1);
  const [sourceType, setSourceType] = useState<"GITHUB" | "OFFLINE_ARCHIVE">("GITHUB");
  const [repoUrl, setRepoUrl] = useState("");
  const [commitSha, setCommitSha] = useState("");
  const [path, setPath] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<SkillImportJob | null>(null);
  const [mock, setMock] = useState<SkillImportMock | null>(null);
  const [conflictAction, setConflictAction] = useState<"CREATE_NEW" | "CREATE_VERSION">("CREATE_NEW");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const preview = () => run(async () => {
    const nextJob = sourceType === "GITHUB"
      ? await previewGitHubSkillImport({ repoUrl, commitSha, path })
      : file
        ? await previewOfflineSkillImport(file, path)
        : null;
    if (!nextJob) throw new Error("请选择离线 ZIP 文件");
    setJob(nextJob);
    setStep(2);
  });

  const executeMock = () => run(async () => {
    if (!job || !job.conversion_result.publishable) throw new Error("该转换结果不可执行 Mock");
    const result = await mockSkillImport(job.id);
    setMock(result);
    setJob(result.job);
  });

  const importSkill = () => run(async () => {
    if (!job || !mock || !confirmed) throw new Error("请先完成 Mock 并确认审计内容");
    const result = await confirmSkillImport(job.id, conflictAction);
    await onImported(result);
  });

  const issues = job?.conversion_result.issues ?? [];
  const blockingIssues = issues.filter((item) => item.severity === "BLOCKING");
  const processing = job?.status === "QUEUED" || job?.status === "FETCHING";

  useEffect(() => {
    if (!job || !processing) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void fetchSkillImportJob(job.id)
        .then((latest) => {
          if (!cancelled) setJob(latest);
        })
        .catch((reason) => {
          if (!cancelled) setError(reason instanceof Error ? reason.message : "读取异步状态失败");
        });
    }, 1200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [job, processing]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto px-4 py-[4vh]">
      <button aria-label="关闭 Skill 导入" className="fixed inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <section
        aria-label="导入外部 Skill"
        aria-modal="true"
        role="dialog"
        className="relative w-full max-w-5xl rounded-3xl bg-white p-6 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-neutral-950">导入外部 Skill</h2>
            <p className="mt-1 text-sm text-neutral-500">外部内容只做静态读取和一次性转换，不执行包内代码。</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-full border px-3 py-1.5 text-sm">关闭</button>
        </div>

        <ol className="mt-6 grid grid-cols-3 gap-2 md:grid-cols-6">
          {STEPS.map((label, index) => {
            const number = index + 1;
            return (
              <li key={label} className={`rounded-xl px-3 py-2 text-center text-xs ${number === step ? "bg-neutral-950 text-white" : number < step ? "bg-emerald-50 text-emerald-700" : "bg-neutral-100 text-neutral-400"}`}>
                {number}. {label}
              </li>
            );
          })}
        </ol>

        {error && <div role="alert" className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

        <div className="mt-6 min-h-80">
          {step === 1 && (
            <div className="space-y-5">
              <div className="grid gap-3 sm:grid-cols-2">
                {(["GITHUB", "OFFLINE_ARCHIVE"] as const).map((value) => (
                  <label key={value} className={`cursor-pointer rounded-2xl border p-4 ${sourceType === value ? "border-neutral-950 bg-neutral-50" : "border-neutral-200"}`}>
                    <input className="mr-2" type="radio" checked={sourceType === value} onChange={() => setSourceType(value)} />
                    <span className="font-medium">{value === "GITHUB" ? "GitHub 固定快照" : "离线 ZIP"}</span>
                    <p className="mt-1 text-xs text-neutral-500">{value === "GITHUB" ? "必须提供完整 40 位 Commit SHA" : "最大 2MB，只允许文本型 Skill 文件"}</p>
                  </label>
                ))}
              </div>
              {sourceType === "GITHUB" ? (
                <div className="grid gap-4">
                  <label className="text-sm">仓库 URL<input value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="https://github.com/org/repo" className="mt-1 w-full rounded-xl border px-3 py-2" /></label>
                  <label className="text-sm">Commit SHA<input value={commitSha} onChange={(event) => setCommitSha(event.target.value)} placeholder="40 位提交哈希，不接受分支或标签" className="mt-1 w-full rounded-xl border px-3 py-2 font-mono" /></label>
                </div>
              ) : (
                <label className="block text-sm">Skill ZIP<input type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} className="mt-1 block w-full rounded-xl border p-3" /></label>
              )}
              <label className="block text-sm">Skill 目录（可选）<input value={path} onChange={(event) => setPath(event.target.value)} placeholder="例如 skills/industry-research" className="mt-1 w-full rounded-xl border px-3 py-2" /></label>
              <button type="button" disabled={busy} onClick={preview} className="rounded-full bg-neutral-950 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50">{busy ? "安全检查中…" : "获取并安全检查"}</button>
            </div>
          )}

          {step === 2 && job && (
            <div>
              <div className={`rounded-2xl border p-5 ${job.status === "FAILED" || blockingIssues.length ? "border-red-200 bg-red-50" : processing ? "border-blue-200 bg-blue-50" : "border-emerald-200 bg-emerald-50"}`}>
                <h3 className="font-semibold">{processing ? "耐久 Worker 正在获取并检查固定快照…" : job.status === "FAILED" ? "安全获取或转换失败" : blockingIssues.length ? `发现 ${blockingIssues.length} 个阻断风险` : "静态安全检查可继续"}</h3>
                <p className="mt-1 text-xs opacity-70">{job.snapshot_hash ? `快照 ${job.snapshot_hash}` : `请求 ${job.request_hash}`} · 到期 {new Date(job.expires_at).toLocaleString()}</p>
                {job.error_message && <p className="mt-3 text-sm text-red-700">{job.error_code}: {job.error_message}</p>}
              </div>
              <div className="mt-4 space-y-2">
                {issues.length === 0 ? <p className="text-sm text-neutral-500">未发现转换风险。</p> : issues.map((issue) => (
                  <div key={`${issue.code}-${issue.path}`} className="rounded-xl border px-4 py-3 text-sm">
                    <span className={`mr-2 rounded-full px-2 py-0.5 text-[11px] ${issue.severity === "BLOCKING" ? "bg-red-100 text-red-700" : issue.severity === "WARNING" ? "bg-amber-100 text-amber-700" : "bg-blue-100 text-blue-700"}`}>{issue.severity}</span>
                    {issue.message} <code className="text-xs text-neutral-400">{issue.path}</code>
                  </div>
                ))}
              </div>
              <div className="mt-6 flex gap-2"><button type="button" onClick={() => setStep(1)} className="rounded-full border px-4 py-2 text-sm">返回</button><button type="button" disabled={processing || !job.conversion_result.publishable} onClick={() => setStep(3)} className="rounded-full bg-neutral-950 px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-30">继续查看转换</button></div>
            </div>
          )}

          {step === 3 && job && (
            <div>
              <h3 className="font-semibold">一次性转换结果</h3>
              <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                <Info label="原格式" value={job.conversion_result.source_format} />
                <Info label="许可证" value={job.conversion_result.license_value || job.conversion_result.license_status} />
                <Info label="已移除字段" value={job.conversion_result.removed_fields.join("、") || "无"} />
                <Info label="推断字段" value={job.conversion_result.inferred_fields.join("、") || "无"} />
                <Info label="缺失必填" value={job.conversion_result.missing_required.join("、") || "无"} />
              </dl>
              <Navigation back={() => setStep(2)} next={() => setStep(4)} nextLabel="查看 Diff" />
            </div>
          )}

          {step === 4 && job && (
            <div>
              <h3 className="font-semibold">原文件与本项目标准文件 Diff</h3>
              <p className="mt-1 text-sm text-neutral-500">绿色新增、红色删除；确认被移除的外部工具与模型声明符合预期。</p>
              <pre className="mt-4 max-h-[52vh] overflow-auto rounded-2xl bg-neutral-950 p-4 text-xs leading-6 text-neutral-100">{job.diff_text || "内容无需转换"}</pre>
              <Navigation back={() => setStep(3)} next={() => setStep(5)} nextLabel="进入 Mock" />
            </div>
          )}

          {step === 5 && job && (
            <div>
              <h3 className="font-semibold">零副作用 Mock</h3>
              <p className="mt-1 text-sm text-neutral-500">只投影编译计划，不读取客户数据，不调用网络、模型或文件写入。</p>
              {!mock ? (
                <button type="button" disabled={busy} onClick={executeMock} className="mt-5 rounded-full bg-neutral-950 px-5 py-2.5 text-sm text-white disabled:opacity-50">{busy ? "Mock 中…" : "执行 Mock"}</button>
              ) : (
                <div className="mt-5 space-y-4">
                  <div className="grid gap-3 sm:grid-cols-3"><Metric label="网络调用" value={mock.network_calls} /><Metric label="模型调用" value={mock.model_calls} /><Metric label="文件写入" value={mock.filesystem_writes} /></div>
                  <pre className="max-h-64 overflow-auto rounded-2xl bg-neutral-50 p-4 text-xs">{JSON.stringify({ name: mock.compiled_name, phase: mock.execution_phase, questions: mock.synthetic_questions, sources: mock.planned_sources, outputs: mock.expected_output_fields }, null, 2)}</pre>
                </div>
              )}
              <Navigation back={() => setStep(4)} next={() => setStep(6)} nextLabel="人工确认" disabled={!mock} />
            </div>
          )}

          {step === 6 && job && mock && (
            <div>
              <h3 className="font-semibold">确认创建本地草稿版本</h3>
              <p className="mt-1 text-sm text-neutral-500">导入后仍需通过本项目黄金用例评测，系统不会自动发布。</p>
              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                <Choice checked={conflictAction === "CREATE_NEW"} onChange={() => setConflictAction("CREATE_NEW")} title="创建新 Skill" description="适用于当前 Workspace 不存在同名 Skill。" />
                <Choice checked={conflictAction === "CREATE_VERSION"} onChange={() => setConflictAction("CREATE_VERSION")} title="创建同名新版本" description="适用于明确要并入已有 Workspace Skill。" />
              </div>
              <label className="mt-5 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm">
                <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-0.5" />
                <span>我已审阅风险、转换内容、完整 Diff 与 Mock 结果，并确认仅创建本地草稿。</span>
              </label>
              <div className="mt-6 flex gap-2"><button type="button" onClick={() => setStep(5)} className="rounded-full border px-4 py-2 text-sm">返回</button><button type="button" disabled={!confirmed || busy} onClick={importSkill} className="rounded-full bg-neutral-950 px-5 py-2 text-sm text-white disabled:opacity-30">{busy ? "导入中…" : "确认导入"}</button></div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function Navigation({ back, next, nextLabel, disabled = false }: { back: () => void; next: () => void; nextLabel: string; disabled?: boolean }) {
  return <div className="mt-6 flex gap-2"><button type="button" onClick={back} className="rounded-full border px-4 py-2 text-sm">返回</button><button type="button" disabled={disabled} onClick={next} className="rounded-full bg-neutral-950 px-4 py-2 text-sm text-white disabled:opacity-30">{nextLabel}</button></div>;
}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="rounded-2xl bg-neutral-50 p-4"><dt className="text-xs text-neutral-400">{label}</dt><dd className="mt-1 break-words text-sm text-neutral-800">{value}</dd></div>;
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div className={`rounded-2xl border p-4 ${value === 0 ? "border-emerald-200 bg-emerald-50" : "border-red-200 bg-red-50"}`}><p className="text-xs text-neutral-500">{label}</p><p className="mt-1 text-2xl font-semibold">{value}</p></div>;
}

function Choice({ checked, onChange, title, description }: { checked: boolean; onChange: () => void; title: string; description: string }) {
  return <label className={`cursor-pointer rounded-2xl border p-4 ${checked ? "border-neutral-950 bg-neutral-50" : "border-neutral-200"}`}><input type="radio" checked={checked} onChange={onChange} className="mr-2" /><span className="font-medium">{title}</span><p className="mt-1 text-xs text-neutral-500">{description}</p></label>;
}
