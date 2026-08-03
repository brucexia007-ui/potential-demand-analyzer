"use client";

// ── 类型 ────────────────────────────────────────────────────────────────────

export type OpportunityScoreData = {
  total_score: number;
  grade: string;         // HIGH / MEDIUM / LOW / INSUFFICIENT
  dimension_scores?: {
    dimension: string;
    weight: number;
    weighted_score: number;
    top_score: number;
    evidence_count: number;
  }[];
  counter_penalty?: number;
  lockin_penalty?: number;
  evidence_count?: number;
  dimension_count?: number;
};

type Props = {
  scoreData: OpportunityScoreData | null;
};

// ── 等级样式 ────────────────────────────────────────────────────────────────

const GRADE_STYLE: Record<string, { label: string; color: string; ring: string; bg: string }> = {
  HIGH: {
    label: "高潜商机",
    color: "text-emerald-700",
    ring: "stroke-emerald-500",
    bg: "bg-emerald-50 border-emerald-200",
  },
  MEDIUM: {
    label: "中潜商机",
    color: "text-blue-700",
    ring: "stroke-blue-500",
    bg: "bg-blue-50 border-blue-200",
  },
  LOW: {
    label: "低潜商机",
    color: "text-yellow-700",
    ring: "stroke-yellow-500",
    bg: "bg-yellow-50 border-yellow-200",
  },
  INSUFFICIENT: {
    label: "证据不足",
    color: "text-neutral-500",
    ring: "stroke-neutral-400",
    bg: "bg-neutral-50 border-neutral-200",
  },
};

const DIM_LABELS: Record<string, string> = {
  bidding_information: "招标信息",
  competitor_analysis: "竞品分析",
  policy_compliance: "政策合规",
  regulatory_changes: "政策变化",
  service_capability: "服务能力",
  qualification: "资质认证",
  feedback: "用户反馈",
  official_pr: "官方信息",
  field_research: "网页背调",
  supplementary: "补充证据",
};

// ── 环形进度条 ──────────────────────────────────────────────────────────────

function RingProgress({
  score,
  size = 96,
  strokeWidth = 8,
  grade,
}: {
  score: number;
  size?: number;
  strokeWidth?: number;
  grade: string;
}) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;
  const style = GRADE_STYLE[grade] || GRADE_STYLE.INSUFFICIENT;

  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width={size} height={size} className="-rotate-90">
        {/* 背景圆环 */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-neutral-100"
        />
        {/* 进度圆环 */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className={`${style.ring} transition-all duration-1000 ease-out`}
        />
      </svg>
      <span className="absolute text-xl font-bold text-neutral-950">
        {Math.round(score)}
      </span>
    </div>
  );
}

// ── 主组件 ──────────────────────────────────────────────────────────────────

export function OpportunityScoreCard({ scoreData }: Props) {
  if (!scoreData) {
    return (
      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-center">
        <p className="text-sm text-neutral-500">商机评分数据暂不可用</p>
      </div>
    );
  }

  const grade = scoreData.grade || "INSUFFICIENT";
  const style = GRADE_STYLE[grade] || GRADE_STYLE.INSUFFICIENT;

  return (
    <div className={`rounded-lg border ${style.bg} p-5 space-y-4`}>
      {/* 头部：总分 + 等级 */}
      <div className="flex items-center gap-5">
        <RingProgress score={scoreData.total_score} grade={grade} />
        <div>
          <span className={`text-sm font-semibold ${style.color}`}>
            {style.label}
          </span>
          <p className="text-2xl font-bold text-neutral-950 mt-0.5">
            {scoreData.total_score}
            <span className="text-sm font-normal text-neutral-500"> / 100</span>
          </p>
          {scoreData.evidence_count !== undefined && (
            <p className="text-xs text-neutral-500 mt-1">
              基于 {scoreData.evidence_count} 条证据 · {scoreData.dimension_count || 0} 个维度
            </p>
          )}
        </div>
      </div>

      {/* 扣分说明 */}
      {(scoreData.counter_penalty ?? 0) > 0 || (scoreData.lockin_penalty ?? 0) > 0 ? (
        <div className="rounded border border-red-200 bg-red-50/70 p-2 text-xs text-red-700">
          {((scoreData.counter_penalty ?? 0) > 0) && (
            <span>反证扣分: -{scoreData.counter_penalty} </span>
          )}
          {((scoreData.lockin_penalty ?? 0) > 0) && (
            <span>竞争锁定扣分: -{scoreData.lockin_penalty}</span>
          )}
        </div>
      ) : null}

      {/* 维度明细 */}
      {scoreData.dimension_scores && scoreData.dimension_scores.length > 0 && (
        <div>
          <p className="text-xs font-medium text-neutral-500 mb-2">各维度评分</p>
          <div className="space-y-1.5">
            {scoreData.dimension_scores.map((ds) => (
              <div key={ds.dimension} className="flex items-center gap-2 text-xs">
                <span className="w-20 text-neutral-600 truncate">
                  {DIM_LABELS[ds.dimension] || ds.dimension}
                </span>
                <div className="flex-1 h-1.5 rounded-full bg-neutral-200 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      ds.weighted_score >= 0.7
                        ? "bg-emerald-500"
                        : ds.weighted_score >= 0.4
                        ? "bg-blue-500"
                        : "bg-yellow-500"
                    }`}
                    style={{ width: `${Math.min(100, (ds.weighted_score / ds.weight) * 100)}%` }}
                  />
                </div>
                <span className="w-10 text-right font-mono text-neutral-700">
                  {(ds.weighted_score * 100).toFixed(0)}%
                </span>
                <span className="w-8 text-right text-neutral-400">
                  ×{ds.evidence_count}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
