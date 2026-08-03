"use client";

// ── 严重度颜色映射 ──────────────────────────────────────────────────────────

const SEVERITY_STYLE: Record<string, { badge: string; border: string; bg: string; label: string }> = {
  fatal: {
    badge: "bg-red-100 text-red-700",
    border: "border-l-red-500",
    bg: "bg-red-50/50",
    label: "致命",
  },
  major: {
    badge: "bg-orange-100 text-orange-700",
    border: "border-l-orange-500",
    bg: "bg-orange-50/50",
    label: "严重",
  },
  minor: {
    badge: "bg-yellow-100 text-yellow-700",
    border: "border-l-yellow-500",
    bg: "bg-yellow-50/50",
    label: "轻微",
  },
  acceptable: {
    badge: "bg-green-100 text-green-700",
    border: "border-l-green-500",
    bg: "bg-green-50/50",
    label: "合格",
  },
};

const SUPPORT_LABELS: Record<string, string> = {
  SUPPORTED: "充分支撑",
  WEAK: "证据偏弱",
  UNSUPPORTED: "证据不足",
  CONTRADICTED: "证据矛盾",
};

// ── 从 evidence_index.audit 提取的类型 ─────────────────────────────────────

export type AuditClaimItem = {
  claim_id: string;
  claim_text: string;
  support_status: string;
  evidence_ids?: string[];
  skeptic_level?: string;
  skeptic_notes?: string;
  suggested_revision?: string;
  severity?: string;           // WBS-20a
  replan_count?: number;        // WBS-20a
};

export type AuditFindingsData = {
  task_id?: string;
  status?: "COMPLETED" | "NOT_APPLICABLE";
  reason_code?: string | null;
  message?: string;
  audited_evidence_count?: number;
  severity?: string;
  fatal_claims?: AuditClaimItem[];
  major_claims?: AuditClaimItem[];
  minor_claims?: AuditClaimItem[];
  claim_audits?: AuditClaimItem[];
  re_plan_suggestions?: string;
};

type Props = {
  auditData: AuditFindingsData | null;
};

export function ClaimAuditPanel({ auditData }: Props) {
  if (!auditData) {
    return (
      <div className="py-12 text-center">
        <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-lg border border-neutral-950/10 bg-white text-xs font-semibold text-neutral-500">
          NIL
        </div>
        <p className="text-sm text-neutral-600">暂无审计数据</p>
        <p className="mt-1 text-xs text-neutral-400">
          任务可能未启用审计管线，或审计尚未完成
        </p>
      </div>
    );
  }

  if (auditData.status === "NOT_APPLICABLE") {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-5">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium text-amber-900">
            审计已完成：无可审计结论
          </h3>
          <span className="rounded-full bg-white px-2 py-0.5 text-xs font-medium text-amber-700">
            已审计证据 {auditData.audited_evidence_count ?? 0} 条
          </span>
        </div>
        <p className="mt-2 text-sm leading-relaxed text-amber-800">
          {auditData.message ||
            "报告没有准入证据支持的可审计结论，本次审计未调用审计模型。"}
        </p>
        <p className="mt-2 text-xs text-amber-700">
          这表示证据质量门未通过，不代表审计管线异常。请先补充有效证据，再重新生成报告。
        </p>
      </div>
    );
  }

  const bucketClaims: AuditClaimItem[] = [
    ...(auditData.fatal_claims || []).map((c) => ({ ...c, severity: c.severity || "fatal" })),
    ...(auditData.major_claims || []).map((c) => ({ ...c, severity: c.severity || "major" })),
    ...(auditData.minor_claims || []).map((c) => ({ ...c, severity: c.severity || "minor" })),
  ];
  const allClaims: AuditClaimItem[] =
    auditData.claim_audits && auditData.claim_audits.length > 0
      ? auditData.claim_audits
      : bucketClaims;

  const fatalCount = auditData.fatal_claims?.length || 0;
  const majorCount = auditData.major_claims?.length || 0;
  const minorCount = auditData.minor_claims?.length || 0;

  if (allClaims.length === 0) {
    return (
      <div className="py-12 text-center">
        <p className="text-sm text-neutral-600">审计数据为空</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* 摘要横幅 */}
      <div className="rounded-lg border border-neutral-950/10 bg-white p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-neutral-700">
            证据审计摘要
            <span className="ml-2 text-xs font-normal text-neutral-500">
              共 {allClaims.length} 条结论
            </span>
          </h3>
          {auditData.severity && (
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${SEVERITY_STYLE[auditData.severity]?.badge || "bg-neutral-100 text-neutral-600"}`}>
              总严重度: {SEVERITY_STYLE[auditData.severity]?.label || auditData.severity}
            </span>
          )}
        </div>

        <div className="flex gap-4 text-sm">
          <div className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-red-500" />
            <span className="text-neutral-600">致命: {fatalCount}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-orange-500" />
            <span className="text-neutral-600">严重: {majorCount}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-yellow-500" />
            <span className="text-neutral-600">轻微: {minorCount}</span>
          </div>
        </div>

        {auditData.re_plan_suggestions && (
          <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-2">
            <p className="text-xs text-amber-700">
              审计建议: {auditData.re_plan_suggestions}
            </p>
          </div>
        )}
      </div>

      {/* 结论列表 */}
      <div className="space-y-3">
        <h3 className="text-sm font-medium text-neutral-700">结论审计详情</h3>

        {allClaims.map((claim, i) => {
          const sev = claim.severity || deriveSeverity(claim);
          const style = SEVERITY_STYLE[sev] || SEVERITY_STYLE.acceptable;

          return (
            <div
              key={claim.claim_id || i}
              className={`rounded-lg border border-neutral-950/10 border-l-4 ${style.border} ${style.bg} p-4`}
            >
              {/* 头部：严重度 + 支撑状态 */}
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium flex-shrink-0 ${style.badge}`}>
                    {style.label}
                  </span>
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${
                    claim.support_status === "SUPPORTED"
                      ? "bg-green-100 text-green-700"
                      : claim.support_status === "CONTRADICTED"
                      ? "bg-red-100 text-red-700"
                      : "bg-yellow-100 text-yellow-700"
                  }`}>
                    {SUPPORT_LABELS[claim.support_status] || claim.support_status}
                  </span>
                  {claim.skeptic_level && claim.skeptic_level !== "NONE" && (
                    <span className="text-xs text-neutral-400">
                      怀疑等级: {claim.skeptic_level}
                    </span>
                  )}
                </div>

                {/* Re-Plan 计数 */}
                {(claim.replan_count ?? 0) > 0 && (
                  <span className="text-xs px-1.5 py-0.5 rounded-full bg-purple-100 text-purple-700 flex-shrink-0 ml-2">
                    Re-Plan ×{claim.replan_count}
                  </span>
                )}
              </div>

              {/* 结论原文 */}
              <p className="text-sm text-neutral-900 mb-3 leading-relaxed">
                {claim.claim_text}
              </p>

              {/* 关联证据 */}
              {claim.evidence_ids && claim.evidence_ids.length > 0 && (
                <div className="mb-2">
                  <span className="text-xs text-neutral-500">关联证据: </span>
                  <span className="text-xs text-neutral-700 font-mono">
                    {claim.evidence_ids.slice(0, 3).map((id) =>
                      typeof id === "string" ? id.slice(0, 8) : String(id).slice(0, 8)
                    ).join(", ")}
                    {claim.evidence_ids.length > 3 && ` ...+${claim.evidence_ids.length - 3}`}
                  </span>
                </div>
              )}

              {/* 审计说明 */}
              {claim.skeptic_notes && (
                <div className="rounded border border-neutral-950/10 bg-white p-2 mb-2">
                  <p className="text-xs text-neutral-600">
                    <span className="font-medium">审计说明: </span>
                    {claim.skeptic_notes}
                  </p>
                </div>
              )}

              {/* 建议修正 */}
              {claim.suggested_revision && (
                <div className="rounded border border-blue-200 bg-blue-50 p-2">
                  <p className="text-xs text-blue-700">
                    <span className="font-medium">建议修正: </span>
                    {claim.suggested_revision}
                  </p>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 对未进入分组列表的审计结论应用同一套确定性严重度规则。 */
function deriveSeverity(claim: AuditClaimItem): string {
  if (claim.support_status === "CONTRADICTED") return "fatal";
  if (claim.support_status === "UNSUPPORTED" && (!claim.evidence_ids || claim.evidence_ids.length === 0)) return "fatal";
  if (claim.support_status === "UNSUPPORTED") return "major";
  if (claim.skeptic_level === "HIGH") return "major";
  if (claim.support_status === "WEAK") return "minor";
  if (claim.skeptic_level === "MEDIUM") return "minor";
  return "acceptable";
}
