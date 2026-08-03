"use client";

import { ChangeEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { StatusBadge } from "@/components/ui/workspace";
import {
  archiveCapabilityDocument,
  listCapabilityDocuments,
  uploadCapabilityDocument,
  type CapabilityKnowledgeDocument,
  type CapabilityProduct,
} from "@/lib/capabilities";

type Props = {
  profileId: string;
  products: CapabilityProduct[];
};

const SIZE_LIMIT = 25 * 1024 * 1024;

export function CapabilityDocumentPanel({ profileId, products }: Props) {
  const [documents, setDocuments] = useState<CapabilityKnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [target, setTarget] = useState("PROFILE");
  const [sensitivity, setSensitivity] = useState<"INTERNAL" | "CONFIDENTIAL" | "RESTRICTED">("INTERNAL");
  const fileInput = useRef<HTMLInputElement>(null);
  const { error: toastError, success: toastSuccess } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      setDocuments(await listCapabilityDocuments(profileId));
    } catch (error) {
      toastError(error instanceof Error ? error.message : "能力资料加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [profileId]);

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > SIZE_LIMIT) {
      toastError("文件不能超过 25MB");
      event.target.value = "";
      return;
    }
    setUploading(true);
    try {
      const productTarget = target.startsWith("PRODUCT:") ? target.slice("PRODUCT:".length) : undefined;
      await uploadCapabilityDocument({
        profileId,
        file,
        entityType: productTarget ? "PRODUCT" : "PROFILE",
        entityId: productTarget,
        sensitivity,
      });
      toastSuccess("资料已解析并进入能力知识库");
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "资料上传失败");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const archive = async (document: CapabilityKnowledgeDocument) => {
    if (!window.confirm(`确认归档“${document.original_filename}”吗？`)) return;
    try {
      await archiveCapabilityDocument(document.id);
      toastSuccess("能力资料已归档");
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "资料归档失败");
    }
  };

  const targetName = (document: CapabilityKnowledgeDocument) => {
    if (document.entity_type === "PROFILE") return "档案级";
    const product = products.find((item) => item.id === document.entity_id);
    return product ? `${product.name} v${product.version_label}` : document.entity_type;
  };

  return (
    <section className="mt-10">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-500">KNOWLEDGE BASE</p>
          <h2 className="mt-1 text-xl font-semibold text-neutral-950">能力资料</h2>
          <p className="mt-1 text-sm text-neutral-600">支持 PDF、Word、PPT、Excel、文本、Markdown 与 CSV；单文件最大 25MB。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={target} onChange={(event) => setTarget(event.target.value)} className="rounded-full border border-neutral-950/20 bg-white px-3 py-2 text-sm">
            <option value="PROFILE">绑定整个档案</option>
            {products.map((product) => <option key={product.id} value={`PRODUCT:${product.id}`}>产品：{product.name} v{product.version_label}</option>)}
          </select>
          <select value={sensitivity} onChange={(event) => setSensitivity(event.target.value as typeof sensitivity)} className="rounded-full border border-neutral-950/20 bg-white px-3 py-2 text-sm">
            <option value="INTERNAL">内部</option>
            <option value="CONFIDENTIAL">机密</option>
            <option value="RESTRICTED">严格受限</option>
          </select>
          <label className={`inline-flex cursor-pointer items-center rounded-full bg-neutral-950 px-4 py-2 text-sm font-medium text-white ${uploading ? "pointer-events-none opacity-50" : ""}`}>
            {uploading ? "解析中…" : "上传资料"}
            <input
              ref={fileInput}
              type="file"
              className="hidden"
              accept=".pdf,.docx,.pptx,.xlsx,.txt,.md,.csv"
              onChange={upload}
              disabled={uploading}
            />
          </label>
        </div>
      </div>

      {loading ? (
        <p className="py-8 text-center text-sm text-neutral-500">正在加载能力资料…</p>
      ) : documents.length === 0 ? (
        <Card variant="bordered" padding="md" className="text-center text-sm text-neutral-500">暂无资料。上传后系统会解析为带来源定位的知识切片。</Card>
      ) : (
        <div className="space-y-2">
          {documents.map((document) => (
            <Card key={document.id} variant="bordered" padding="sm" className="flex flex-wrap items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="max-w-md truncate text-sm font-medium text-neutral-950">{document.original_filename}</p>
                  <span className="rounded bg-neutral-950/5 px-2 py-0.5 text-xs text-neutral-600">v{document.version_no}</span>
                  <StatusBadge status={document.status} label={document.status === "READY" ? "可检索" : document.status} />
                </div>
                <p className="mt-1 text-xs text-neutral-500">
                  {targetName(document)} · {document.chunk_count} 个知识切片 · {(document.size_bytes / 1024).toFixed(1)} KB · {document.sensitivity}
                </p>
              </div>
              <Button size="sm" variant="ghost" onClick={() => archive(document)}>归档</Button>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
