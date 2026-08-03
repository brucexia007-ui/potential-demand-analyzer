"use client";

import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { CreateCapabilityProductPayload } from "@/lib/capabilities";

type Props = {
  saving: boolean;
  onSubmit: (payload: CreateCapabilityProductPayload) => Promise<void>;
  onCancel: () => void;
};

const lines = (value: string) => Array.from(new Set(
  value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
));

function ListField({ label, value, onChange, required = false }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  return (
    <label className="block text-sm font-medium text-neutral-700">
      {label}{required ? " *" : ""}
      <textarea
        className="mt-2 min-h-28 w-full rounded-lg border border-neutral-950/20 bg-white/90 px-4 py-3 text-sm text-neutral-950 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/20"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="每行一项"
      />
    </label>
  );
}

export function CapabilityProductForm({ saving, onSubmit, onCancel }: Props) {
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [productLine, setProductLine] = useState("");
  const [summary, setSummary] = useState("");
  const [capabilities, setCapabilities] = useState("");
  const [constraints, setConstraints] = useState("");
  const [unsuitable, setUnsuitable] = useState("");
  const [differentiators, setDifferentiators] = useState("");
  const [regions, setRegions] = useState("");
  const [industries, setIndustries] = useState("");
  const [status, setStatus] = useState<"DRAFT" | "ACTIVE">("DRAFT");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await onSubmit({
      name: name.trim(),
      version_label: version.trim(),
      product_line: productLine.trim() || undefined,
      summary: summary.trim(),
      capabilities: lines(capabilities).map((item) => ({ name: item })),
      constraints: lines(constraints).map((item) => ({ name: item })),
      unsuitable_scenarios: lines(unsuitable).map((item) => ({ name: item })),
      differentiators: lines(differentiators).map((item) => ({ name: item })),
      supported_regions: lines(regions),
      supported_industries: lines(industries),
      status,
    });
  };

  const activeReady = Boolean(summary.trim() && lines(capabilities).length > 0);

  return (
    <form className="space-y-5" onSubmit={submit}>
      <div className="grid gap-4 md:grid-cols-3">
        <Input label="产品名称 *" value={name} onChange={(event) => setName(event.target.value)} maxLength={255} />
        <Input label="版本 *" value={version} onChange={(event) => setVersion(event.target.value)} maxLength={100} />
        <Input label="产品线" value={productLine} onChange={(event) => setProductLine(event.target.value)} maxLength={255} />
      </div>
      <label className="block text-sm font-medium text-neutral-700">
        产品摘要{status === "ACTIVE" ? " *" : ""}
        <textarea
          className="mt-2 min-h-32 w-full rounded-lg border border-neutral-950/20 bg-white/90 px-4 py-3 text-sm text-neutral-950 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/20"
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
          maxLength={20000}
        />
      </label>
      <div className="grid gap-4 md:grid-cols-2">
        <ListField label="核心能力" value={capabilities} onChange={setCapabilities} required={status === "ACTIVE"} />
        <ListField label="能力边界/限制" value={constraints} onChange={setConstraints} />
        <ListField label="不适用场景" value={unsuitable} onChange={setUnsuitable} />
        <ListField label="差异化能力" value={differentiators} onChange={setDifferentiators} />
        <ListField label="支持地区" value={regions} onChange={setRegions} />
        <ListField label="支持行业" value={industries} onChange={setIndustries} />
      </div>
      <label className="block text-sm font-medium text-neutral-700">
        初始状态
        <select value={status} onChange={(event) => setStatus(event.target.value as "DRAFT" | "ACTIVE")} className="mt-2 w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 md:w-64">
          <option value="DRAFT">草稿（暂不参与匹配）</option>
          <option value="ACTIVE">启用（参与自动匹配）</option>
        </select>
      </label>
      {status === "ACTIVE" && !activeReady && <p className="text-sm text-amber-700">启用产品必须填写摘要和至少一项核心能力。</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onCancel}>取消</Button>
        <Button type="submit" variant="primary" disabled={saving || !name.trim() || !version.trim() || (status === "ACTIVE" && !activeReady)}>
          {saving ? "创建中…" : "创建产品版本"}
        </Button>
      </div>
    </form>
  );
}
