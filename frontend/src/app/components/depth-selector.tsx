"use client";

type DepthKey = "quick" | "standard" | "deep";

type DepthOption = {
  key: DepthKey;
  label: string;
  desc: string;
  iterations: number;
  evidenceGoal: string;
  estimatedTime: string;
  costLevel: string;
  bestFor: string;
};

export const DEPTH_OPTIONS: DepthOption[] = [
  {
    key: "quick",
    label: "快速版",
    desc: "少量搜索，低成本，适合初筛",
    iterations: 1,
    evidenceGoal: "3 条/维度",
    estimatedTime: "3-5 分钟",
    costLevel: "低",
    bestFor: "快速判断客户是否值得跟进，一次搜索快速出结论",
  },
  {
    key: "standard",
    label: "标准版",
    desc: "默认模式，质量和成本平衡",
    iterations: 2,
    evidenceGoal: "5 条/维度",
    estimatedTime: "5-8 分钟",
    costLevel: "中",
    bestFor: "日常调研场景，足够支撑售前方案，性价比最优",
  },
  {
    key: "deep",
    label: "深度版",
    desc: "重点客户，更多证据，更全面",
    iterations: 3,
    evidenceGoal: "8 条/维度",
    estimatedTime: "10-15 分钟",
    costLevel: "高",
    bestFor: "重要客户/大单，需要充分证据支撑投标和方案",
  },
];

type Props = {
  value: string;
  onChange: (key: string) => void;
};

export function DepthSelector({ value, onChange }: Props) {
  const selected = DEPTH_OPTIONS.find((d) => d.key === value);

  return (
    <div className="space-y-3">
      {/* 分段选择器 */}
      <div className="grid grid-cols-3 gap-2">
        {DEPTH_OPTIONS.map((d) => (
          <button
            key={d.key}
            type="button"
            onClick={() => onChange(d.key)}
            className={`rounded-lg border p-3 text-center transition-all ${
              value === d.key
                ? "border-neutral-950 bg-neutral-950 text-white"
                : "border-neutral-950/20 bg-white text-neutral-800 opacity-70 hover:opacity-100"
            }`}
          >
            <div className="text-sm font-medium">{d.label}</div>
            <div className={`text-xs mt-0.5 ${value === d.key ? "text-white/70" : "text-neutral-500"}`}>
              {d.desc}
            </div>
          </button>
        ))}
      </div>

      {/* 选中详情 */}
      {selected && (
        <div className="rounded-lg border border-neutral-950/10 bg-neutral-50 p-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            <div>
              <span className="text-neutral-500">搜索轮数：</span>
              <span className="font-medium text-neutral-800">{selected.iterations} 轮</span>
            </div>
            <div>
              <span className="text-neutral-500">证据目标：</span>
              <span className="font-medium text-neutral-800">{selected.evidenceGoal}</span>
            </div>
            <div>
              <span className="text-neutral-500">预估耗时：</span>
              <span className="font-medium text-neutral-800">{selected.estimatedTime}</span>
            </div>
            <div>
              <span className="text-neutral-500">Token 消耗：</span>
              <span className={`font-medium ${
                selected.costLevel === "高" ? "text-yellow-700" : "text-neutral-800"
              }`}>{selected.costLevel}</span>
            </div>
          </div>
          <p className="mt-2 text-xs text-neutral-500">{selected.bestFor}</p>
          {selected.key === "deep" && (
            <p className="mt-1 text-xs text-yellow-600">
              ⚠ 深度版可能消耗较多 API Token，请确认预算充足
            </p>
          )}
        </div>
      )}
    </div>
  );
}
