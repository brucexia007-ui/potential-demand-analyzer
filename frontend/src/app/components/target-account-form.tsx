"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { TargetAccountInput } from "@/lib/target-accounts";

type TargetAccountFormProps = {
  submitting?: boolean;
  onSubmit: (input: TargetAccountInput) => Promise<void> | void;
  onCancel: () => void;
};

const OPTIONAL_FIELDS: Array<{ key: keyof Omit<TargetAccountInput, "input_name">; label: string; placeholder: string }> = [
  { key: "official_name", label: "工商全称", placeholder: "可选，用于消歧" },
  { key: "website", label: "官网", placeholder: "https://example.com" },
  { key: "credit_code", label: "统一社会信用代码", placeholder: "可选" },
  { key: "industry", label: "行业", placeholder: "如：金融" },
  { key: "region", label: "地区", placeholder: "如：上海" },
  { key: "stock_code", label: "股票代码", placeholder: "可选" },
];

export function TargetAccountForm({ submitting = false, onSubmit, onCancel }: TargetAccountFormProps) {
  const [inputName, setInputName] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedName = inputName.trim();
    if (!normalizedName) {
      setError("请输入目标企业名称");
      return;
    }
    setError(null);
    const optional = Object.fromEntries(
      Object.entries(fields).filter(([, value]) => value.trim()),
    ) as Omit<TargetAccountInput, "input_name">;
    await onSubmit({ input_name: normalizedName, ...optional });
  };

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <div>
        <label htmlFor="target-input-name" className="mb-1.5 block text-sm font-medium text-neutral-900">
          目标企业名称 <span className="text-red-600">*</span>
        </label>
        <input
          id="target-input-name"
          autoFocus
          value={inputName}
          onChange={(event) => setInputName(event.target.value)}
          placeholder="例如：上海某某银行"
          className="w-full rounded-lg border border-neutral-950/15 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-950 focus:ring-2 focus:ring-neutral-950/10"
        />
      </div>

      <details className="rounded-lg border border-neutral-950/10 bg-neutral-50/50 p-3">
        <summary className="cursor-pointer text-sm font-medium text-neutral-700">补充企业信息（可选，用于消歧）</summary>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {OPTIONAL_FIELDS.map((field) => (
            <label key={field.key} className="text-sm text-neutral-700">
              <span className="mb-1 block">{field.label}</span>
              <input
                value={fields[field.key] ?? ""}
                onChange={(event) => setFields((current) => ({ ...current, [field.key]: event.target.value }))}
                placeholder={field.placeholder}
                className="w-full rounded-lg border border-neutral-950/15 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-950 focus:ring-2 focus:ring-neutral-950/10"
              />
            </label>
          ))}
        </div>
      </details>

      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onCancel} disabled={submitting}>取消</Button>
        <Button type="submit" variant="primary" disabled={submitting}>{submitting ? "创建中…" : "创建目标企业"}</Button>
      </div>
    </form>
  );
}
