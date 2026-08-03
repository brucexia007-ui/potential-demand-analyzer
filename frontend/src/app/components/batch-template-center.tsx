"use client";

import { useEffect, useState } from "react";
import {
  downloadBatchTemplate,
  listBatchTemplates,
  type BatchTemplateDefinition,
} from "@/lib/batch-import";

type Props = {
  selectedId: BatchTemplateDefinition["template_id"];
  onSelect: (templateId: BatchTemplateDefinition["template_id"]) => void;
  onError: (message: string) => void;
};

export function BatchTemplateCenter({ selectedId, onSelect, onError }: Props) {
  const [templates, setTemplates] = useState<BatchTemplateDefinition[]>([]);
  const [downloading, setDownloading] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listBatchTemplates()
      .then((items) => { if (!cancelled) setTemplates(items); })
      .catch((error) => { if (!cancelled) onError(error instanceof Error ? error.message : "模板目录加载失败"); });
    return () => { cancelled = true; };
  }, []);

  const download = async (template: BatchTemplateDefinition, format: "xlsx" | "csv") => {
    const key = `${template.template_id}:${format}`;
    setDownloading(key);
    try {
      await downloadBatchTemplate(template.template_id, format);
    } catch (error) {
      onError(error instanceof Error ? error.message : "模板下载失败");
    } finally {
      setDownloading(null);
    }
  };

  return (
    <section className="rounded-xl border border-neutral-950/10 bg-neutral-50 p-4">
      <div>
        <p className="text-xs font-semibold tracking-[0.14em] text-neutral-500">TEMPLATE CENTER</p>
        <h3 className="mt-1 text-base font-semibold text-neutral-950">先选择模板，降低批量输入错误</h3>
        <p className="mt-1 text-sm text-neutral-600">推荐 XLSX：内含填写说明、独立示例页和机器可读版本信息。</p>
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {templates.map((template) => {
          const selected = template.template_id === selectedId;
          const required = template.fields.filter((field) => field.required).map((field) => field.label);
          return (
            <article
              key={template.template_id}
              className={`rounded-xl border p-4 ${selected ? "border-neutral-950 bg-white" : "border-neutral-950/10 bg-white/60"}`}
            >
              <button type="button" onClick={() => onSelect(template.template_id)} className="w-full text-left">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-neutral-950">{template.name}</span>
                  <span className="rounded-full bg-neutral-100 px-2 py-1 text-[11px] text-neutral-600">v{template.version}</span>
                </div>
                <p className="mt-2 text-sm leading-5 text-neutral-600">{template.description}</p>
                <p className="mt-2 text-xs text-neutral-500">必填：{required.join("、")}</p>
              </button>
              <div className="mt-4 flex gap-2">
                {(["xlsx", "csv"] as const).map((format) => (
                  <button
                    key={format}
                    type="button"
                    disabled={downloading !== null}
                    onClick={() => void download(template, format)}
                    className="rounded-full border border-neutral-950/15 bg-white px-3 py-1.5 text-xs font-medium uppercase text-neutral-800 hover:border-neutral-950 disabled:opacity-50"
                  >
                    {downloading === `${template.template_id}:${format}` ? "下载中…" : `下载 ${format}`}
                  </button>
                ))}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
