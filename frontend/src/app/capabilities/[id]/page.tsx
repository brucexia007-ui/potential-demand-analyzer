"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { CapabilityProductForm } from "@/app/components/capability-product-form";
import { CapabilityDocumentPanel } from "@/app/components/capability-document-panel";
import { CapabilityPortfolioPanel } from "@/app/components/capability-portfolio-panel";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell, StatusBadge } from "@/components/ui/workspace";
import {
  archiveCapabilityProduct,
  createCapabilityProduct,
  getCapabilityProfile,
  listCapabilityProducts,
  type CapabilityProduct,
  type CapabilityProfile,
  type CreateCapabilityProductPayload,
} from "@/lib/capabilities";

const entryNames = (items: Array<Record<string, unknown>>) => items
  .map((item) => String(item.name || item.title || "").trim())
  .filter(Boolean);

export default function CapabilityProfilePage() {
  const params = useParams<{ id: string }>();
  const profileId = params.id;
  const [profile, setProfile] = useState<CapabilityProfile | null>(null);
  const [products, setProducts] = useState<CapabilityProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const { error: toastError, success: toastSuccess } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      const [profileResult, productResult] = await Promise.all([
        getCapabilityProfile(profileId),
        listCapabilityProducts(profileId),
      ]);
      setProfile(profileResult);
      setProducts(productResult);
    } catch (error) {
      toastError(error instanceof Error ? error.message : "产品档案加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [profileId]);

  const createProduct = async (payload: CreateCapabilityProductPayload) => {
    setSaving(true);
    try {
      await createCapabilityProduct(profileId, payload);
      toastSuccess("产品版本已创建");
      setShowForm(false);
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "产品版本创建失败");
    } finally {
      setSaving(false);
    }
  };

  const archiveProduct = async (product: CapabilityProduct) => {
    if (!window.confirm(`确认归档“${product.name} ${product.version_label}”吗？`)) return;
    setSaving(true);
    try {
      await archiveCapabilityProduct(product.id);
      toastSuccess("产品版本已归档");
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "产品归档失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <PageShell>
      <Link href="/capabilities" className="mb-4 inline-flex text-sm text-neutral-600 hover:text-neutral-950">← 返回能力中心</Link>
      <PageHeader
        eyebrow="PRODUCT PORTFOLIO"
        title={profile?.name || "能力档案"}
        description="每次重大调整都创建新的产品版本；已启用版本不可原地改写，以保证历史匹配与报告可审计。"
        action={<Button variant="primary" onClick={() => setShowForm(true)} disabled={!profile}>新建产品版本</Button>}
      />

      {showForm && (
        <Card variant="bordered" padding="lg" className="mb-6">
          <h2 className="mb-5 text-base font-medium text-neutral-950">创建产品版本</h2>
          <CapabilityProductForm saving={saving} onSubmit={createProduct} onCancel={() => setShowForm(false)} />
        </Card>
      )}

      {loading ? (
        <div className="py-16 text-center text-sm text-neutral-600">正在加载产品版本…</div>
      ) : products.length === 0 ? (
        <Card variant="bordered" padding="lg" className="py-16 text-center">
          <h2 className="text-lg font-medium text-neutral-950">还没有产品版本</h2>
          <p className="mt-2 text-sm text-neutral-600">录入产品能力和明确限制后，系统才能进行可信的客户需求匹配。</p>
          <Button variant="primary" className="mt-5" onClick={() => setShowForm(true)}>创建第一个产品版本</Button>
        </Card>
      ) : (
        <div className="space-y-4">
          {products.map((product) => (
            <Card key={product.id} variant="bordered" padding="md">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-semibold text-neutral-950">{product.name}</h2>
                    <span className="rounded bg-neutral-950/5 px-2 py-1 text-xs text-neutral-600">v{product.version_label}</span>
                    <StatusBadge status={product.status} label={product.status === "ACTIVE" ? "已启用" : "草稿"} />
                  </div>
                  <p className="mt-1 text-sm text-neutral-500">{product.product_line || "未设置产品线"}</p>
                </div>
                <Button size="sm" variant="ghost" disabled={saving} onClick={() => archiveProduct(product)}>归档版本</Button>
              </div>
              <p className="mt-4 whitespace-pre-wrap text-sm leading-6 text-neutral-700">{product.summary || "暂无摘要"}</p>
              <div className="mt-5 grid gap-4 border-t border-neutral-950/10 pt-5 md:grid-cols-2 xl:grid-cols-4">
                {[
                  ["核心能力", entryNames(product.capabilities)],
                  ["能力限制", entryNames(product.constraints)],
                  ["不适用场景", entryNames(product.unsuitable_scenarios)],
                  ["差异化", entryNames(product.differentiators)],
                ].map(([label, values]) => (
                  <div key={label as string}>
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">{label as string}</h3>
                    {(values as string[]).length ? (
                      <ul className="mt-2 space-y-1 text-sm text-neutral-700">{(values as string[]).map((value) => <li key={value}>• {value}</li>)}</ul>
                    ) : <p className="mt-2 text-sm text-neutral-400">未填写</p>}
                  </div>
                ))}
              </div>
              <div className="mt-4 flex flex-wrap gap-2 text-xs text-neutral-600">
                {product.supported_industries.map((item) => <span key={`industry-${item}`} className="rounded-full bg-blue-50 px-2.5 py-1">行业：{item}</span>)}
                {product.supported_regions.map((item) => <span key={`region-${item}`} className="rounded-full bg-emerald-50 px-2.5 py-1">地区：{item}</span>)}
              </div>
            </Card>
          ))}
        </div>
      )}
      <CapabilityPortfolioPanel profileId={profileId} products={products} />
      <CapabilityDocumentPanel profileId={profileId} products={products} />
    </PageShell>
  );
}
