"use client";

import type { BatchCandidateRow, FieldMappingItem } from "@/lib/batch-import";

const FIELD_LABELS: Record<string, string> = {
  company_name: "企业名称（必填）",
  demand_direction: "需求方向（必填）",
  industry: "行业（可选）",
  region: "地区（可选）",
  official_website: "官网（可选）",
  unified_social_credit_code: "统一社会信用代码（可选）",
  capability_profile_id: "企业能力档案 ID（可选）",
};

type Props = {
  mapping: FieldMappingItem[];
  previewRows: BatchCandidateRow[];
  onConfirm: () => void;
  onBack: () => void;
};

export function BatchFieldMapper({ mapping, previewRows, onConfirm, onBack }: Props) {
  const mapped = mapping.filter((m) => m.confidence !== "manual");
  const warnings: string[] = [];

  if (!mapped.find((m) => m.standard_field === "industry")) {
    warnings.push("未检测到「行业」列，将留空");
  }
  if (!mapped.find((m) => m.standard_field === "region")) {
    warnings.push("未检测到「地区」列，将留空");
  }

  return (
    <div className="space-y-6">
      {/* 字段映射表 */}
      <div>
        <h3 className="text-sm font-medium text-neutral-700 mb-3">字段识别结果</h3>
        <div className="rounded-lg border border-neutral-950/10 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50">
              <tr>
                <th className="text-left px-4 py-2 text-neutral-500 font-medium">原始列名</th>
                <th className="text-left px-4 py-2 text-neutral-500 font-medium">→ 映射为</th>
                <th className="text-center px-4 py-2 text-neutral-500 font-medium">置信度</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {mapping.map((m) => (
                <tr key={m.standard_field}>
                  <td className="px-4 py-2 font-mono text-neutral-800">{m.detected_header}</td>
                  <td className="px-4 py-2 text-neutral-600">{FIELD_LABELS[m.standard_field] || m.standard_field}</td>
                  <td className="px-4 py-2 text-center">
                    <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                      m.confidence === "high"
                        ? "bg-green-100 text-green-700"
                        : "bg-yellow-100 text-yellow-700"
                    }`}>
                      {m.confidence === "high" ? "高" : "中"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {warnings.length > 0 && (
          <div className="mt-3 rounded-lg border border-yellow-200 bg-yellow-50 p-3">
            {warnings.map((w) => (
              <p key={w} className="text-sm text-yellow-700">{w}</p>
            ))}
          </div>
        )}
      </div>

      {/* 数据预览 */}
      <div>
        <h3 className="text-sm font-medium text-neutral-700 mb-3">
          数据预览（前 {previewRows.length} 行）
        </h3>
        <div className="max-h-60 overflow-y-auto rounded-lg border border-neutral-950/10">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-neutral-50">
              <tr>
                <th className="text-left px-3 py-2 text-neutral-500 font-medium">#</th>
                <th className="text-left px-3 py-2 text-neutral-500 font-medium">企业</th>
                <th className="text-left px-3 py-2 text-neutral-500 font-medium">需求</th>
                <th className="text-left px-3 py-2 text-neutral-500 font-medium">行业</th>
                <th className="text-left px-3 py-2 text-neutral-500 font-medium">地区</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {previewRows.map((row) => (
                <tr key={row.source_row_index}>
                  <td className="px-3 py-1.5 text-neutral-400">{row.source_row_index}</td>
                  <td className="px-3 py-1.5 text-neutral-900 truncate max-w-[160px]">{row.company_name}</td>
                  <td className="px-3 py-1.5 text-neutral-600 truncate max-w-[200px]">{row.demand_direction}</td>
                  <td className="px-3 py-1.5 text-neutral-500">{row.industry || "-"}</td>
                  <td className="px-3 py-1.5 text-neutral-500">{row.region || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 按钮 */}
      <div className="flex gap-3">
        <button
          type="button"
          onClick={onConfirm}
          className="inline-flex items-center rounded-full bg-neutral-950 px-5 py-2.5 text-sm font-medium text-white hover:bg-neutral-800"
        >
          确认映射，继续验证
        </button>
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center rounded-full border border-neutral-950/10 bg-white px-5 py-2.5 text-sm text-neutral-950 hover:bg-neutral-100"
        >
          返回重新上传
        </button>
      </div>
    </div>
  );
}
