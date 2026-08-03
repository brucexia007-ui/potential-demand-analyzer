"use client";

import { Button } from "@/components/ui/button";
import type { PlanResult } from "@/lib/advisor";

const PROFILE_LABELS: Record<string, string> = {
  sales_brief: "销售极简版",
  presales_standard: "售前标准版",
  technical_deep: "技术深度版",
  management_summary: "管理摘要版",
};

const DEPTH_INFO: Record<string, { label: string }> = {
  quick: { label: "快速版" },
  standard: { label: "标准版" },
  deep: { label: "深度版" },
};

type Props = {
  companyName: string;
  demandDirection: string;
  industry: string;
  region: string;
  reportProfile: string;
  depth: string;
  planResult: PlanResult;
  enableFieldAgent: boolean;
  onConfirm: () => void;
  onBack: () => void;
  isCreating: boolean;
};

export function ResearchPlanPreview({
  companyName,
  demandDirection,
  industry,
  region,
  reportProfile,
  depth,
  planResult,
  enableFieldAgent,
  onConfirm,
  onBack,
  isCreating,
}: Props) {
  const depthInfo = DEPTH_INFO[depth] || DEPTH_INFO.standard;
  const profileLabel = PROFILE_LABELS[reportProfile] || reportProfile;

  return (
    <div className="space-y-5">
      <h3 className="text-lg font-semibold text-neutral-900">调研计划预览</h3>

      {/* 任务摘要 */}
      <div className="rounded-lg border border-neutral-950/10 bg-neutral-50 p-4">
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div>
            <span className="text-neutral-500">客户：</span>
            <span className="font-medium text-neutral-900">{companyName}</span>
          </div>
          <div>
            <span className="text-neutral-500">需求：</span>
            <span className="font-medium text-neutral-900">{demandDirection}</span>
          </div>
          {industry && (
            <div>
              <span className="text-neutral-500">行业：</span>
              <span className="font-medium text-neutral-900">{industry}</span>
            </div>
          )}
          {region && (
            <div>
              <span className="text-neutral-500">地区：</span>
              <span className="font-medium text-neutral-900">{region}</span>
            </div>
          )}
          <div>
            <span className="text-neutral-500">报告视角：</span>
            <span className="font-medium text-neutral-900">{profileLabel}</span>
          </div>
          <div>
            <span className="text-neutral-500">深度：</span>
            <span className="font-medium text-neutral-900">{depthInfo.label}</span>
          </div>
        </div>
      </div>

      {/* 商业分析目标 */}
      <div>
        <h4 className="mb-2 text-sm font-medium text-neutral-700">最终要支持的商业决策</h4>
        <div className="rounded-lg border border-neutral-950/10 p-4">
          <p className="font-medium text-neutral-950">{planResult.analysis_objective}</p>
          <ul className="mt-3 grid gap-2 text-sm text-neutral-600 md:grid-cols-2">
            {planResult.decision_questions.map((question) => (
              <li key={question} className="rounded-md bg-neutral-50 px-3 py-2">
                {question}
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* 候选关注点 */}
      {planResult.candidate_focus.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-medium text-neutral-700">候选关注点</h4>
          <div className="flex flex-wrap gap-2">
            {planResult.candidate_focus.map((focus) => (
              <span key={focus} className="rounded-full bg-neutral-950 px-3 py-1 text-xs font-medium text-white">
                {focus}
              </span>
            ))}
          </div>
          <p className="mt-2 text-xs text-neutral-500">
            这些是预览关注点。目标主体确认后，LLM 会重新构建目标树、任务 DAG、来源和精确查询。
          </p>
        </div>
      )}

      {/* 执行护栏 */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-neutral-950/10 p-3 text-center">
          <div className="text-xs text-neutral-500 mb-1">最多查询</div>
          <div className="text-lg font-semibold text-neutral-900">
            {planResult.budget_guardrails.max_search_queries}
          </div>
        </div>
        <div className="rounded-lg border border-neutral-950/10 p-3 text-center">
          <div className="text-xs text-neutral-500 mb-1">最多抓取</div>
          <div className="text-lg font-semibold text-neutral-900">
            {planResult.budget_guardrails.max_fetches}
          </div>
        </div>
        <div className="rounded-lg border border-neutral-950/10 p-3 text-center">
          <div className="text-xs text-neutral-500 mb-1">证据缺口重规划</div>
          <div className="text-lg font-semibold text-neutral-900">
            最多 {planResult.budget_guardrails.max_replan_rounds} 次
          </div>
        </div>
      </div>

      {/* 外部 Agent */}
      <div className="rounded-lg border border-neutral-950/10 p-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-neutral-700">网页体验背调</span>
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
            enableFieldAgent ? "bg-green-100 text-green-700" : "bg-neutral-100 text-neutral-500"
          }`}>
            {enableFieldAgent ? "已启用" : "未启用"}
          </span>
        </div>
        {enableFieldAgent && (
          <p className="mt-1 text-xs text-neutral-500">
            系统将使用浏览器访问目标企业公开网页，观察服务入口和页面体验。不会登录、提交或下载。
          </p>
        )}
      </div>

      {/* 成本预估 */}
      <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-4">
        <h4 className="text-sm font-medium text-yellow-800 mb-2">成本估算（仅供参考）</h4>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span className="text-yellow-700">规划方式：</span>
            <span className="font-medium text-yellow-900">LLM 动态目标树与任务 DAG</span>
          </div>
          <div>
            <span className="text-yellow-700">预估耗时：</span>
            <span className="font-medium text-yellow-900">
              {depth === "quick" ? "3-5 分钟" : depth === "deep" ? "10-15 分钟" : "5-8 分钟"}
            </span>
          </div>
        </div>
        {depth === "deep" && (
          <p className="mt-2 text-xs text-yellow-600">
            深度版可能会消耗较多 API Token，请确认预算充足。
          </p>
        )}
      </div>

      {/* AI 推荐理由 */}
      {planResult.reasoning && (
        <div className="rounded-lg border border-neutral-950/10 p-3">
          <h4 className="text-sm font-medium text-neutral-700 mb-1">AI 推荐理由</h4>
          <p className="text-sm text-neutral-600">{planResult.reasoning}</p>
        </div>
      )}

      {/* 按钮 */}
      <div className="flex gap-3 pt-2">
        <Button variant="primary" size="lg" onClick={onConfirm} isLoading={isCreating}>
          {isCreating ? "创建中..." : "确认创建任务"}
        </Button>
        <Button variant="secondary" size="lg" onClick={onBack}>返回修改</Button>
      </div>
    </div>
  );
}
