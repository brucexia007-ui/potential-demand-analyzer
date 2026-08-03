"use client";

import { useEffect, useState } from "react";
import { SkillEvalPanel } from "@/app/components/skill-eval-panel";
import { SkillForm } from "@/app/components/skill-form";
import { SkillGraphEditor } from "@/app/components/skill-graph-editor";
import { SkillImportWizard } from "@/app/components/skill-import-wizard";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import {
  archiveSkill,
  createSkill,
  createSkillVersion,
  dryRunSkill,
  fetchSkillDetail,
  fetchSkillSource,
  fetchSkills,
  publishSkill,
  type SkillDetail,
  type SkillDryRun,
  type SkillSummary,
} from "@/lib/skills";

type EditorState = {
  detail: SkillDetail | null;
  source: string;
  loading: boolean;
} | null;

const STATUS_LABELS: Record<string, string> = {
  DRAFT: "草稿",
  COMPILED: "已编译",
  EVALUATED: "已评测",
  PUBLISHED: "已发布",
  REJECTED: "未通过评测",
  ARCHIVED: "已归档",
};

export default function SkillsSettingsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editor, setEditor] = useState<EditorState>(null);
  const [dryRun, setDryRun] = useState<SkillDryRun | null>(null);
  const [evalSkill, setEvalSkill] = useState<SkillSummary | null>(null);
  const [graphSkill, setGraphSkill] = useState<SkillSummary | null>(null);
  const [showImporter, setShowImporter] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const { error: toastError, success: toastSuccess } = useToast();

  const load = async () => {
    setLoading(true);
    const result = await fetchSkills();
    if (result === null) toastError("加载 Skill 列表失败");
    else setSkills(result);
    setLoading(false);
  };

  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const openEditor = async (skill?: SkillSummary) => {
    if (!skill) {
      setEditor({ detail: null, source: "", loading: false });
      return;
    }
    setEditor({ detail: null, source: "", loading: true });
    const detail = await fetchSkillDetail(skill.id);
    const selectedVersion = detail?.latest_version;
    const source = detail && selectedVersion
      ? await fetchSkillSource(detail.id, selectedVersion.id)
      : null;
    if (!detail || source === null) {
      setEditor(null);
      toastError("加载 Skill 源文件失败");
      return;
    }
    setEditor({ detail, source, loading: false });
  };

  const saveEditor = async (value: { markdown: string; displayName?: string }) => {
    setSaving(true);
    try {
      if (editor?.detail) {
        await createSkillVersion(editor.detail.id, value.markdown);
        toastSuccess("新版本已编译保存，请先预演再发布");
      } else {
        await createSkill(value.markdown, value.displayName);
        toastSuccess("Skill 已创建并编译，请先预演再发布");
      }
      setEditor(null);
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const runDryRun = async (skill: SkillSummary) => {
    if (!skill.latest_version) return;
    setBusyId(skill.id);
    try {
      setDryRun(await dryRunSkill(skill.id, skill.latest_version.id));
    } catch (error) {
      toastError(error instanceof Error ? error.message : "预演失败");
    } finally {
      setBusyId(null);
    }
  };

  const publish = async (skill: SkillSummary) => {
    if (!skill.latest_version) return;
    setBusyId(skill.id);
    try {
      await publishSkill(skill.id, skill.latest_version.id);
      toastSuccess(`${skill.display_name} v${skill.latest_version.version} 已发布`);
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "发布失败");
    } finally {
      setBusyId(null);
    }
  };

  const archive = async (skill: SkillSummary) => {
    if (!window.confirm(`确认归档“${skill.display_name}”？归档后不能再用于新任务。`)) return;
    setBusyId(skill.id);
    try {
      await archiveSkill(skill.id);
      toastSuccess("Skill 已归档");
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "归档失败");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <PageShell>
      <PageHeader
        title="Skills 专家策略"
        description="通过引导表单维护标准 SKILL.md；每次修改形成不可变版本，经预演和黄金用例评测后发布。"
      />

      <div className="mb-5 flex items-center justify-between rounded-2xl border border-blue-200 bg-blue-50 px-4 py-3">
        <div>
          <p className="text-sm font-medium text-blue-950">普通用户无需编辑策略</p>
          <p className="mt-0.5 text-xs text-blue-700">创建任务时只会看到已发布的一级 Skill；此页面面向售前专家和管理员。</p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={() => setShowImporter(true)}
            className="rounded-full border border-blue-300 bg-white px-4 py-2 text-sm font-medium text-blue-800"
          >
            导入外部 Skill
          </button>
          <button
            type="button"
            onClick={() => openEditor()}
            className="rounded-full bg-neutral-950 px-4 py-2 text-sm font-medium text-white"
          >
            新建 Skill
          </button>
        </div>
      </div>

      {loading ? (
        <div className="py-16 text-center text-sm text-neutral-400">加载中…</div>
      ) : skills.length === 0 ? (
        <div className="py-16 text-center text-sm text-neutral-400">暂无 Skill</div>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {skills.map((skill) => {
            const latest = skill.latest_version;
            const hasUnpublishedVersion = Boolean(latest && latest.id !== skill.current_version_id);
            return (
              <Card key={skill.id} variant="bordered" padding="md">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="font-semibold text-neutral-950">{skill.display_name}</h2>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] ${skill.scope === "SYSTEM" ? "bg-violet-50 text-violet-700" : "bg-emerald-50 text-emerald-700"}`}>
                        {skill.scope === "SYSTEM" ? "系统只读" : "Workspace"}
                      </span>
                      <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-600">
                        {STATUS_LABELS[skill.status] || skill.status}
                      </span>
                    </div>
                    <code className="mt-1 block truncate text-xs text-neutral-400">{skill.name}</code>
                    <p className="mt-3 line-clamp-2 text-sm leading-6 text-neutral-600">{skill.description}</p>
                    <div className="mt-3 flex gap-3 text-xs text-neutral-400">
                      <span>最新 v{latest?.version ?? "—"}</span>
                      <span>{latest ? STATUS_LABELS[latest.status] || latest.status : "无版本"}</span>
                      {hasUnpublishedVersion && <span className="text-amber-700">待发布</span>}
                    </div>
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap gap-2 border-t border-neutral-950/10 pt-3">
                  <button type="button" onClick={() => openEditor(skill)} className="rounded-full border px-3 py-1.5 text-xs">
                    {skill.editable ? "编辑新版本" : "查看源文件"}
                  </button>
                  {latest && (
                    <button type="button" onClick={() => setGraphSkill(skill)} className="rounded-full border px-3 py-1.5 text-xs">
                      两级编排
                    </button>
                  )}
                  {latest && (
                    <button type="button" onClick={() => runDryRun(skill)} disabled={busyId === skill.id} className="rounded-full border px-3 py-1.5 text-xs disabled:opacity-50">
                      Dry Run
                    </button>
                  )}
                  {skill.editable && hasUnpublishedVersion && latest && ["COMPILED", "REJECTED"].includes(latest.status) && (
                    <button type="button" onClick={() => setEvalSkill(skill)} disabled={busyId === skill.id} className="rounded-full border border-neutral-900 px-3 py-1.5 text-xs disabled:opacity-50">
                      评测 v{latest.version}
                    </button>
                  )}
                  {skill.editable && hasUnpublishedVersion && latest?.status === "EVALUATED" && (
                    <button type="button" onClick={() => publish(skill)} disabled={busyId === skill.id} className="rounded-full bg-neutral-950 px-3 py-1.5 text-xs text-white disabled:opacity-50">
                      发布 v{latest?.version}
                    </button>
                  )}
                  {skill.editable && (
                    <button type="button" onClick={() => archive(skill)} disabled={busyId === skill.id} className="ml-auto px-2 py-1.5 text-xs text-red-500 disabled:opacity-50">
                      归档
                    </button>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {editor && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto px-4 py-[6vh]">
          <button aria-label="关闭" className="fixed inset-0 bg-black/35 backdrop-blur-sm" onClick={() => setEditor(null)} />
          <div className="relative w-full max-w-4xl rounded-2xl bg-white p-6 shadow-2xl">
            {editor.loading ? (
              <div className="py-16 text-center text-sm text-neutral-500">加载源文件…</div>
            ) : (
              <SkillForm
                skill={editor.detail}
                source={editor.source}
                targetVersion={editor.detail
                  ? editor.detail.latest_version!.version + (editor.detail.editable ? 1 : 0)
                  : 1}
                readOnly={Boolean(editor.detail && !editor.detail.editable)}
                onSave={saveEditor}
                onCancel={() => setEditor(null)}
                isSaving={saving}
              />
            )}
          </div>
        </div>
      )}

      {dryRun && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
          <button aria-label="关闭" className="fixed inset-0 bg-black/35" onClick={() => setDryRun(null)} />
          <div className="relative w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl">
            <h3 className="text-lg font-semibold">Dry Run 执行预览</h3>
            <p className="mt-1 text-xs text-neutral-500">本次预演未调用模型、搜索、抓取或外部文件。</p>
            <div className="mt-4 space-y-2">
              {dryRun.tool_plan.map((item) => <div key={item} className="rounded-xl bg-neutral-50 px-3 py-2 font-mono text-xs">{item}</div>)}
            </div>
            <pre className="mt-4 overflow-auto rounded-xl bg-neutral-950 p-3 text-xs text-neutral-100">{JSON.stringify(dryRun.budget, null, 2)}</pre>
            <button type="button" onClick={() => setDryRun(null)} className="mt-5 rounded-full bg-neutral-950 px-4 py-2 text-sm text-white">关闭</button>
          </div>
        </div>
      )}

      {evalSkill && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto px-4 py-[4vh]">
          <button aria-label="关闭评测" className="fixed inset-0 bg-black/35 backdrop-blur-sm" onClick={() => setEvalSkill(null)} />
          <div className="relative w-full max-w-5xl rounded-2xl bg-white p-6 shadow-2xl">
            <SkillEvalPanel
              skill={evalSkill}
              onClose={() => setEvalSkill(null)}
              onEvaluated={load}
            />
          </div>
        </div>
      )}

      {showImporter && (
        <SkillImportWizard
          onClose={() => setShowImporter(false)}
          onImported={async (result) => {
            setShowImporter(false);
            toastSuccess(`${result.skill.display_name} v${result.version.version} 已导入为本地草稿`);
            await load();
          }}
        />
      )}
      {graphSkill && (
        <SkillGraphEditor
          root={graphSkill}
          skills={skills}
          onClose={() => setGraphSkill(null)}
          onSaved={async () => {
            toastSuccess("Skill 编排已保存为新的不可变版本");
            await load();
          }}
        />
      )}
    </PageShell>
  );
}
