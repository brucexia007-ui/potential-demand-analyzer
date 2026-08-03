"use client";

import type { ReactNode } from "react";

type ProfileKey = "sales_brief" | "presales_standard" | "technical_deep" | "management_summary";

type ProfileOption = {
  key: ProfileKey;
  label: string;
  desc: string;
  color: string;
  scenario: string;
  structure: string[];
  targetUser: string;
};

export const PROFILE_OPTIONS: ProfileOption[] = [
  {
    key: "sales_brief",
    label: "销售极简版",
    desc: "痛点、动态、评分、下一步动作",
    color: "border-l-green-400",
    scenario: "快速扫一眼客户是否有机会，适合客户拜访前的 5 分钟准备",
    structure: ["痛点摘要", "商机信号", "下一步动作"],
    targetUser: "销售/客户经理",
  },
  {
    key: "presales_standard",
    label: "售前标准版",
    desc: "五维分析、证据、话术、切入点",
    color: "border-l-blue-400",
    scenario: "全面了解客户需求全景，输出结构化分析报告，适合售前方案准备",
    structure: ["客户画像", "多维分析", "证据清单", "切入建议"],
    targetUser: "售前工程师",
  },
  {
    key: "technical_deep",
    label: "技术深度版",
    desc: "招标参数、系统现状、集成风险",
    color: "border-l-purple-400",
    scenario: "深度分析客户技术栈和招标要求，识别技术风险，适合撰写技术方案",
    structure: ["技术栈推断", "集成风险评估", "招标参数倾向", "方案建议"],
    targetUser: "技术架构师",
  },
  {
    key: "management_summary",
    label: "管理摘要版",
    desc: "批量排序、高潜客户、资源投入",
    color: "border-l-amber-400",
    scenario: "批量筛选高潜客户，对商机价值排序，辅助管理层决策资源投放",
    structure: ["商机总览", "高潜排序", "资源投入建议"],
    targetUser: "销售总监/管理层",
  },
];

type Props = {
  value: string;
  onChange: (key: string) => void;
};

export function ProfileSelector({ value, onChange }: Props) {
  const selected = PROFILE_OPTIONS.find((p) => p.key === value);

  return (
    <div className="space-y-3">
      {/* 分段选择器 */}
      <div className="grid grid-cols-2 gap-2">
        {PROFILE_OPTIONS.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => onChange(p.key)}
            className={`rounded-lg border border-l-4 bg-white p-3 text-left transition-all ${
              value === p.key
                ? `${p.color} border-neutral-950 ring-1 ring-neutral-950/10`
                : `${p.color} border-transparent opacity-70 hover:opacity-100`
            }`}
          >
            <div className="text-sm font-medium text-neutral-800">{p.label}</div>
            <div className="text-xs text-neutral-500 mt-0.5">{p.desc}</div>
          </button>
        ))}
      </div>

      {/* 选中详情 */}
      {selected && (
        <div className="rounded-lg border border-neutral-950/10 bg-neutral-50 p-3 text-sm space-y-1.5">
          <div>
            <span className="text-neutral-500">适用：</span>
            <span className="text-neutral-800">{selected.targetUser}</span>
          </div>
          <div>
            <span className="text-neutral-500">场景：</span>
            <span className="text-neutral-800">{selected.scenario}</span>
          </div>
          <div>
            <span className="text-neutral-500">结构：</span>
            <span className="text-neutral-800">{selected.structure.join(" → ")}</span>
          </div>
        </div>
      )}
    </div>
  );
}
