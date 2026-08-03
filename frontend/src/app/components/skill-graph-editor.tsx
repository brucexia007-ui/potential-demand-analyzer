"use client";

import { useEffect, useMemo, useState } from "react";
import {
  createSkillVersion,
  fetchSkillGraph,
  previewSkillGraph,
  type SkillGraph,
  type SkillGraphEdge,
  type SkillGraphPreview,
  type SkillSummary,
} from "@/lib/skills";

type Clause = NonNullable<SkillGraphEdge["condition"]["all"]>[number];
type EditableEdge = Pick<SkillGraphEdge, "child_skill_id" | "min_version" | "condition">;

type Props = {
  root: SkillSummary;
  skills: SkillSummary[];
  onClose: () => void;
  onSaved: () => Promise<void>;
};

const FIELDS = [
  ["research_mode", "研究模式"],
  ["industry", "行业"],
  ["region", "地区"],
  ["gate_level", "阶段门"],
  ["has_customer_private", "有客户私有材料"],
  ["product_selected", "已选择产品"],
] as const;
const OPERATORS = ["EQ", "NEQ", "IN", "NOT_IN", "EXISTS"] as const;
const BOOLEAN_FIELDS = new Set(["has_customer_private", "product_selected"]);

function scalarValue(value: string): string | boolean {
  if (value === "true") return true;
  if (value === "false") return false;
  return value;
}

function displayValue(value: Clause["value"]): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}

function updateClauseValue(clause: Clause, raw: string): Clause["value"] {
  if (clause.operator === "IN" || clause.operator === "NOT_IN") {
    return raw.split(",").map((item) => scalarValue(item.trim())).filter((item) => item !== "");
  }
  return scalarValue(raw);
}

export function SkillGraphEditor({ root, skills, onClose, onSaved }: Props) {
  const version = root.latest_version;
  const [graph, setGraph] = useState<SkillGraph | null>(null);
  const [edges, setEdges] = useState<EditableEdge[]>([]);
  const [preview, setPreview] = useState<SkillGraphPreview | null>(null);
  const [candidateId, setCandidateId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const readOnly = !root.editable;
  const candidates = useMemo(
    () => skills.filter((skill) =>
      skill.id !== root.id
      && skill.status === "PUBLISHED"
      && !edges.some((edge) => edge.child_skill_id === skill.id)),
    [edges, root.id, skills],
  );
  const nodesById = useMemo(
    () => new Map(graph?.nodes.map((node) => [node.skill_id, node]) || []),
    [graph],
  );

  useEffect(() => {
    if (!version) return;
    setLoading(true);
    fetchSkillGraph(root.id, version.id)
      .then((value) => {
        setGraph(value);
        setEdges(value.edges.map((edge) => ({
          child_skill_id: edge.child_skill_id,
          min_version: edge.min_version,
          condition: edge.condition,
        })));
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "加载编排失败"))
      .finally(() => setLoading(false));
  }, [root.id, version]);

  const mutate = (next: EditableEdge[]) => {
    setEdges(next);
    setPreview(null);
    setError(null);
  };

  const addEdge = () => {
    if (!candidateId) return;
    mutate([...edges, { child_skill_id: candidateId, min_version: 1, condition: {} }]);
    setCandidateId("");
  };

  const updateEdge = (index: number, patch: Partial<EditableEdge>) => {
    mutate(edges.map((edge, current) => current === index ? { ...edge, ...patch } : edge));
  };

  const updateClause = (edgeIndex: number, clauseIndex: number, patch: Partial<Clause>) => {
    const edge = edges[edgeIndex];
    const clauses = [...(edge.condition.all || [])];
    const next = { ...clauses[clauseIndex], ...patch } as Clause;
    if (patch.field && BOOLEAN_FIELDS.has(patch.field)) next.value = true;
    if (patch.operator === "EXISTS") next.value = true;
    clauses[clauseIndex] = next;
    updateEdge(edgeIndex, { condition: { all: clauses } });
  };

  const addClause = (edgeIndex: number) => {
    const edge = edges[edgeIndex];
    updateEdge(edgeIndex, {
      condition: {
        all: [...(edge.condition.all || []), {
          field: "research_mode",
          operator: "EQ",
          value: "OPPORTUNITY_DISCOVERY",
        }],
      },
    });
  };

  const runPreview = async () => {
    if (!version) return;
    setBusy(true);
    setError(null);
    try {
      setPreview(await previewSkillGraph(root.id, version.id, edges));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "预览失败");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      await createSkillVersion(root.id, preview.markdown);
      await onSaved();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto px-4 py-[4vh]">
      <button aria-label="关闭编排" className="fixed inset-0 bg-black/35 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-6xl rounded-2xl bg-white p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-neutral-950">Skill 两级编排</h3>
            <p className="mt-1 text-sm text-neutral-500">{root.display_name} · 当前 v{version?.version ?? "-"}；修改会生成新的不可变版本。</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-full border px-4 py-2 text-sm">关闭</button>
        </div>

        {loading ? <p className="py-16 text-center text-sm text-neutral-500">加载编排中…</p> : graph && (
          <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.65fr)]">
            <section className="space-y-4">
              <div className="rounded-2xl border-2 border-neutral-950 bg-neutral-950 p-4 text-white">
                <div className="text-xs text-neutral-400">一级 Skill · ROOT</div>
                <div className="mt-1 font-semibold">{graph.nodes[0]?.display_name}</div>
                <div className="mt-2 text-xs text-neutral-300">权限包络：{graph.nodes[0]?.allowed_tools.join(" · ") || "未声明工具"}</div>
              </div>
              <div className="text-center text-xl text-neutral-300">↓</div>

              {!readOnly && (
                <div className="flex gap-2 rounded-2xl border border-dashed p-3">
                  <select value={candidateId} onChange={(event) => setCandidateId(event.target.value)} className="min-w-0 flex-1 rounded-xl border px-3 py-2 text-sm">
                    <option value="">选择已发布的二级 Skill</option>
                    {candidates.map((skill) => <option key={skill.id} value={skill.id}>{skill.display_name}</option>)}
                  </select>
                  <button type="button" onClick={addEdge} disabled={!candidateId} className="rounded-xl bg-neutral-950 px-4 text-sm text-white disabled:opacity-40">添加节点</button>
                </div>
              )}

              <div className="grid gap-3">
                {edges.length === 0 && <div className="rounded-2xl bg-amber-50 p-4 text-sm text-amber-800">尚未配置二级 Skill。一级编排发布前至少应有一个可执行节点。</div>}
                {edges.map((edge, edgeIndex) => {
                  const node = nodesById.get(edge.child_skill_id);
                  const fallback = skills.find((skill) => skill.id === edge.child_skill_id);
                  const clauses = edge.condition.all || [];
                  return (
                    <div key={edge.child_skill_id} className="rounded-2xl border p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="font-medium">{node?.display_name || fallback?.display_name || edge.child_skill_id}</div>
                          <div className="mt-1 text-xs text-neutral-500">阶段：{node?.execution_phase || "待预览解析"} · 最低版本</div>
                        </div>
                        {!readOnly && <button type="button" onClick={() => mutate(edges.filter((_, index) => index !== edgeIndex))} className="text-xs text-red-600">移除</button>}
                      </div>
                      <input type="number" min={1} value={edge.min_version} disabled={readOnly} onChange={(event) => updateEdge(edgeIndex, { min_version: Math.max(1, Number(event.target.value) || 1) })} className="mt-2 w-24 rounded-lg border px-2 py-1 text-sm" />

                      <div className="mt-4 border-t pt-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium text-neutral-600">启用条件（全部满足才执行）</span>
                          {!readOnly && <button type="button" onClick={() => addClause(edgeIndex)} className="text-xs text-blue-700">+ 添加条件</button>}
                        </div>
                        {clauses.length === 0 && <p className="mt-2 text-xs text-neutral-400">无条件：每次任务都会执行</p>}
                        <div className="mt-2 space-y-2">
                          {clauses.map((clause, clauseIndex) => (
                            <div key={`${edge.child_skill_id}-${clauseIndex}`} className="grid gap-2 rounded-xl bg-neutral-50 p-2 sm:grid-cols-[1fr_100px_1fr_auto]">
                              <select value={clause.field} disabled={readOnly} onChange={(event) => updateClause(edgeIndex, clauseIndex, { field: event.target.value })} className="rounded-lg border px-2 py-1.5 text-xs">
                                {FIELDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                              </select>
                              <select value={clause.operator} disabled={readOnly} onChange={(event) => updateClause(edgeIndex, clauseIndex, { operator: event.target.value as Clause["operator"] })} className="rounded-lg border px-2 py-1.5 text-xs">
                                {OPERATORS.map((value) => <option key={value}>{value}</option>)}
                              </select>
                              {(clause.operator === "EXISTS" || BOOLEAN_FIELDS.has(clause.field)) ? (
                                <select value={String(clause.value)} disabled={readOnly} onChange={(event) => updateClause(edgeIndex, clauseIndex, { value: event.target.value === "true" })} className="rounded-lg border px-2 py-1.5 text-xs">
                                  <option value="true">是</option><option value="false">否</option>
                                </select>
                              ) : (
                                <input value={displayValue(clause.value)} disabled={readOnly} onChange={(event) => updateClause(edgeIndex, clauseIndex, { value: updateClauseValue(clause, event.target.value) })} placeholder={clause.operator.includes("IN") ? "多个值用逗号分隔" : "比较值"} className="rounded-lg border px-2 py-1.5 text-xs" />
                              )}
                              {!readOnly && <button type="button" onClick={() => updateEdge(edgeIndex, { condition: { all: clauses.filter((_, index) => index !== clauseIndex) } })} className="px-1 text-xs text-red-600">×</button>}
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            <aside className="space-y-4">
              <div className="rounded-2xl bg-neutral-50 p-4">
                <h4 className="text-sm font-semibold">确定性执行顺序</h4>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(preview?.graph.execution_order || graph.execution_order).map((name, index) => <span key={name} className="rounded-full bg-white px-2.5 py-1 text-xs ring-1 ring-neutral-200">{index + 1}. {name}</span>)}
                </div>
              </div>
              {preview ? (
                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                  <h4 className="text-sm font-semibold text-emerald-900">预览通过 · 将生成 v{preview.compiled_version}</h4>
                  <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-xl bg-neutral-950 p-3 text-[11px] leading-5 text-neutral-100">{preview.diff_text || "无内容变化"}</pre>
                </div>
              ) : <p className="rounded-2xl bg-blue-50 p-4 text-xs leading-5 text-blue-800">保存前必须先预览。系统会校验两层限制、循环依赖、版本约束以及父子 Skill 的工具和数据域权限。</p>}
              {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
              {!readOnly && (
                <div className="flex gap-2">
                  <button type="button" onClick={runPreview} disabled={busy} className="flex-1 rounded-full border border-neutral-950 px-4 py-2 text-sm disabled:opacity-50">{busy ? "处理中…" : "校验并预览"}</button>
                  <button type="button" onClick={save} disabled={busy || !preview} className="flex-1 rounded-full bg-neutral-950 px-4 py-2 text-sm text-white disabled:opacity-40">保存为新版本</button>
                </div>
              )}
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}
