"use client";

import { FormEvent, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { StatusBadge } from "@/components/ui/workspace";
import {
  archiveCapabilityPortfolioItem,
  createCapabilityCase,
  createCapabilityQualification,
  createCapabilitySolution,
  listCapabilityCases,
  listCapabilityQualifications,
  listCapabilitySolutions,
  type CapabilityCase,
  type CapabilityProduct,
  type CapabilityQualification,
  type CapabilitySolution,
} from "@/lib/capabilities";

type Props = { profileId: string; products: CapabilityProduct[] };
type PortfolioType = "solutions" | "cases" | "qualifications";
type Status = "DRAFT" | "ACTIVE";

const splitLines = (value: string) => Array.from(new Set(
  value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
));
const toIsoDate = (value: string) => value ? `${value}T00:00:00Z` : undefined;
const textareaClass = "mt-2 min-h-28 w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-3 text-sm text-neutral-950 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/20";

function ProductSelector({ products, value, onChange }: {
  products: CapabilityProduct[];
  value: string[];
  onChange: (value: string[]) => void;
}) {
  const activeProducts = products.filter((item) => item.status !== "ARCHIVED");
  return (
    <fieldset>
      <legend className="text-sm font-medium text-neutral-700">关联产品</legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {activeProducts.length === 0 && <span className="text-sm text-neutral-400">暂无可关联产品</span>}
        {activeProducts.map((product) => (
          <label key={product.id} className="flex items-center gap-2 rounded-full border border-neutral-950/15 px-3 py-2 text-sm">
            <input
              type="checkbox"
              checked={value.includes(product.id)}
              onChange={(event) => onChange(event.target.checked
                ? [...value, product.id]
                : value.filter((id) => id !== product.id))}
            />
            {product.name} v{product.version_label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function StatusSelector({ value, onChange }: { value: Status; onChange: (value: Status) => void }) {
  return (
    <label className="block text-sm font-medium text-neutral-700">
      初始状态
      <select value={value} onChange={(event) => onChange(event.target.value as Status)} className="mt-2 w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 md:w-64">
        <option value="DRAFT">草稿（不参与自动匹配）</option>
        <option value="ACTIVE">启用（参与自动匹配）</option>
      </select>
    </label>
  );
}

export function CapabilityPortfolioPanel({ profileId, products }: Props) {
  const [tab, setTab] = useState<PortfolioType>("solutions");
  const [solutions, setSolutions] = useState<CapabilitySolution[]>([]);
  const [cases, setCases] = useState<CapabilityCase[]>([]);
  const [qualifications, setQualifications] = useState<CapabilityQualification[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<Status>("DRAFT");
  const [name, setName] = useState("");
  const [secondary, setSecondary] = useState("");
  const [body, setBody] = useState("");
  const [result, setResult] = useState("");
  const [listText, setListText] = useState("");
  const [productIds, setProductIds] = useState<string[]>([]);
  const [qualificationType, setQualificationType] = useState<CapabilityQualification["qualification_type"]>("CERTIFICATION");
  const [certificateNo, setCertificateNo] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [validTo, setValidTo] = useState("");
  const { error: toastError, success: toastSuccess } = useToast();

  const load = async () => {
    try {
      const [solutionItems, caseItems, qualificationItems] = await Promise.all([
        listCapabilitySolutions(profileId),
        listCapabilityCases(profileId),
        listCapabilityQualifications(profileId),
      ]);
      setSolutions(solutionItems);
      setCases(caseItems);
      setQualifications(qualificationItems);
    } catch (error) {
      toastError(error instanceof Error ? error.message : "能力组合加载失败");
    }
  };

  useEffect(() => { void load(); }, [profileId]);

  const reset = () => {
    setName(""); setSecondary(""); setBody(""); setResult(""); setListText("");
    setProductIds([]); setCertificateNo(""); setValidFrom(""); setValidTo("");
    setStatus("DRAFT"); setQualificationType("CERTIFICATION"); setShowForm(false);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      if (tab === "solutions") {
        await createCapabilitySolution(profileId, {
          name: name.trim(), industry: secondary.trim() || undefined,
          problem_statement: body.trim(), solution_summary: result.trim(), product_ids: productIds,
          constraints: splitLines(listText).map((item) => ({ name: item })), status,
        });
      } else if (tab === "cases") {
        await createCapabilityCase(profileId, {
          title: name.trim(), customer_industry: secondary.trim() || undefined,
          challenge: body.trim(), outcome: result.trim(), product_ids: productIds,
          metrics: splitLines(listText).map((item) => ({ name: item })), status,
        });
      } else {
        await createCapabilityQualification(profileId, {
          qualification_type: qualificationType, name: name.trim(), issuer: secondary.trim() || undefined,
          certificate_no: certificateNo.trim() || undefined, applicable_regions: splitLines(listText),
          valid_from: toIsoDate(validFrom), valid_to: toIsoDate(validTo), status,
        });
      }
      toastSuccess("能力条目已创建");
      reset();
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "能力条目创建失败");
    } finally {
      setSaving(false);
    }
  };

  const archive = async (itemType: PortfolioType, id: string) => {
    if (!window.confirm("确认归档该能力条目吗？")) return;
    try {
      await archiveCapabilityPortfolioItem(itemType, id);
      toastSuccess("能力条目已归档");
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "能力条目归档失败");
    }
  };

  const tabs: Array<[PortfolioType, string, number]> = [
    ["solutions", "行业方案", solutions.length],
    ["cases", "客户案例", cases.length],
    ["qualifications", "资质证照", qualifications.length],
  ];
  const items = tab === "solutions" ? solutions : tab === "cases" ? cases : qualifications;

  return (
    <section className="mt-10">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-500">CAPABILITY PORTFOLIO</p>
          <h2 className="mt-1 text-xl font-semibold text-neutral-950">方案、案例与资质</h2>
          <p className="mt-1 text-sm text-neutral-600">结构化沉淀可匹配能力；客户需求仍必须由外部或客户私有证据证明。</p>
        </div>
        <Button variant="primary" onClick={() => setShowForm(true)}>新增{tabs.find(([key]) => key === tab)?.[1]}</Button>
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        {tabs.map(([key, label, count]) => (
          <Button key={key} size="sm" variant={tab === key ? "primary" : "secondary"} onClick={() => { setTab(key); reset(); }}>
            {label} {count}
          </Button>
        ))}
      </div>

      {showForm && (
        <Card variant="bordered" padding="lg" className="mb-4">
          <form className="space-y-5" onSubmit={submit}>
            <div className="grid gap-4 md:grid-cols-2">
              <Input label={`${tab === "cases" ? "案例标题" : tab === "solutions" ? "方案名称" : "资质名称"} *`} value={name} onChange={(event) => setName(event.target.value)} />
              {tab === "qualifications" ? (
                <label className="text-sm font-medium text-neutral-700">资质类型
                  <select value={qualificationType} onChange={(event) => setQualificationType(event.target.value as typeof qualificationType)} className="mt-2 w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5">
                    <option value="CERTIFICATION">认证</option><option value="QUALIFICATION">资质</option>
                    <option value="LICENSE">许可</option><option value="SECURITY">安全认证</option><option value="OTHER">其他</option>
                  </select>
                </label>
              ) : <Input label={tab === "solutions" ? "适用行业" : "客户行业"} value={secondary} onChange={(event) => setSecondary(event.target.value)} />}
            </div>

            {tab === "qualifications" ? (
              <>
                <div className="grid gap-4 md:grid-cols-2">
                  <Input label="颁发机构" value={secondary} onChange={(event) => setSecondary(event.target.value)} />
                  <Input label="证书编号" value={certificateNo} onChange={(event) => setCertificateNo(event.target.value)} />
                  <Input label="有效期开始" type="date" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} />
                  <Input label="有效期结束" type="date" value={validTo} onChange={(event) => setValidTo(event.target.value)} />
                </div>
                <label className="block text-sm font-medium text-neutral-700">适用地区（每行一项）
                  <textarea className={textareaClass} value={listText} onChange={(event) => setListText(event.target.value)} />
                </label>
              </>
            ) : (
              <>
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="block text-sm font-medium text-neutral-700">{tab === "solutions" ? "客户问题" : "客户挑战"}{status === "ACTIVE" ? " *" : ""}
                    <textarea className={textareaClass} value={body} onChange={(event) => setBody(event.target.value)} />
                  </label>
                  <label className="block text-sm font-medium text-neutral-700">{tab === "solutions" ? "方案摘要" : "实施结果"}{status === "ACTIVE" ? " *" : ""}
                    <textarea className={textareaClass} value={result} onChange={(event) => setResult(event.target.value)} />
                  </label>
                </div>
                <ProductSelector products={products} value={productIds} onChange={setProductIds} />
                <label className="block text-sm font-medium text-neutral-700">{tab === "solutions" ? "限制条件" : "量化成效"}（每行一项）
                  <textarea className={textareaClass} value={listText} onChange={(event) => setListText(event.target.value)} />
                </label>
              </>
            )}
            <StatusSelector value={status} onChange={setStatus} />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={reset}>取消</Button>
              <Button type="submit" variant="primary" disabled={saving || !name.trim() || (status === "ACTIVE" && tab !== "qualifications" && (!body.trim() || !result.trim()))}>
                {saving ? "保存中…" : "创建条目"}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {items.length === 0 ? (
        <Card variant="bordered" padding="md" className="text-center text-sm text-neutral-500">暂无{tabs.find(([key]) => key === tab)?.[1]}</Card>
      ) : (
        <div className="space-y-2">
          {items.map((item) => {
            const title = "title" in item ? item.title : item.name;
            const description = "solution_summary" in item ? item.solution_summary : "outcome" in item ? item.outcome : [item.issuer, item.certificate_no].filter(Boolean).join(" · ");
            return (
              <Card key={item.id} variant="bordered" padding="sm" className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2"><p className="text-sm font-medium text-neutral-950">{title}</p><StatusBadge status={item.status} label={item.status === "ACTIVE" ? "已启用" : "草稿"} /></div>
                  <p className="mt-1 max-w-3xl whitespace-pre-wrap text-sm text-neutral-600">{description || "暂无摘要"}</p>
                </div>
                <Button size="sm" variant="ghost" onClick={() => archive(tab, item.id)}>归档</Button>
              </Card>
            );
          })}
        </div>
      )}
    </section>
  );
}
