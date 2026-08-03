"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { interpretInput, planTask, createTask, type InterpretResult, type PlanResult, type CreateTaskPayload } from "@/lib/advisor";
import { type RuntimeSkillBrief, fetchRuntimeSkills } from "@/lib/skills";
import { createTargetAccount } from "@/lib/target-accounts";
import { ResearchPlanPreview } from "@/app/components/research-plan-preview";
import { ProfileSelector } from "@/app/components/profile-selector";
import { DepthSelector } from "@/app/components/depth-selector";
import { useConfig } from "@/components/providers/config-provider";

// ── 步骤枚举 ────────────────────────────────────────────────────────────

type Step = "input" | "form" | "plan" | "creating";

// ── 组件 Props ─────────────────────────────────────────────────────────

export function SmartTaskForm() {
  const router = useRouter();
  const { error: toastError } = useToast();
  const { status: configStatus, error: configStatusError } = useConfig();

  // 步骤
  const [step, setStep] = useState<Step>("input");

  // NLP 输入
  const [nlpInput, setNlpInput] = useState("");
  const [isInterpreting, setIsInterpreting] = useState(false);

  // 解析结果
  const [interpretResult, setInterpretResult] = useState<InterpretResult | null>(null);

  // 表单字段
  const [companyName, setCompanyName] = useState("");
  const [demandDirection, setDemandDirection] = useState("");
  const [industry, setIndustry] = useState("");
  const [region, setRegion] = useState("");
  const [businessGoal, setBusinessGoal] = useState("");
  const [selectedSkillName, setSelectedSkillName] = useState("pilot-opportunity");
  const [reportProfile, setReportProfile] = useState<string>("presales_standard");
  const [depth, setDepth] = useState<string>("standard");
  const [enableFieldAgent, setEnableFieldAgent] = useState(false);

  // Plan 结果
  const [planResult, setPlanResult] = useState<PlanResult | null>(null);
  const [isPlanning, setIsPlanning] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  // Skill 列表
  const [skillOptions, setSkillOptions] = useState<RuntimeSkillBrief[]>([]);
  const [skillsLoaded, setSkillsLoaded] = useState(false);
  const [skillLoadError, setSkillLoadError] = useState<string | null>(null);

  // ── 加载 Skill 列表 ──────────────────────────────────────────────

  const loadSkills = async (): Promise<RuntimeSkillBrief[]> => {
    if (skillsLoaded) return skillOptions;
    const skills = await fetchRuntimeSkills();
    if (!skills || skills.length === 0) {
      setSkillLoadError("没有可执行的标准 Skill，请联系管理员检查 SKILL.md 目录");
      setSkillOptions([]);
      setSkillsLoaded(true);
      return [];
    }
    setSkillOptions(skills);
    setSkillLoadError(null);
    if (!skills.some((skill) => skill.name === selectedSkillName)) {
      setSelectedSkillName(skills[0].name);
    }
    setSkillsLoaded(true);
    return skills;
  };

  // ── 解析自然语言 ─────────────────────────────────────────────────

  const handleInterpret = async () => {
    if (!configStatus?.execution_ready) {
      toastError(configStatusError ? "无法确认系统执行状态，请稍后重试" : "当前为浏览模式，请先完成模型与搜索连接验证");
      return;
    }
    if (!nlpInput.trim()) {
      toastError("请输入客户需求描述");
      return;
    }
    setIsInterpreting(true);
    try {
      const result = await interpretInput(nlpInput.trim());
      setInterpretResult(result);

      // 预填表单
      setCompanyName(result.company_name || "");
      setDemandDirection(result.demand_direction || "");
      setIndustry(result.industry || "");
      setRegion(result.region || "");
      setBusinessGoal(result.business_goal || "");
      setStep("form");
      const runtimeSkills = await loadSkills();
      if (
        result.suggested_skill &&
        runtimeSkills.some((skill) => skill.name === result.suggested_skill)
      ) {
        setSelectedSkillName(result.suggested_skill);
      }
    } catch (err) {
      toastError(err instanceof Error ? err.message : "解析失败");
    } finally {
      setIsInterpreting(false);
    }
  };

  // ── 跳过解析，直接填表 ───────────────────────────────────────────

  const skipInterpret = () => {
    void loadSkills();
    setStep("form");
  };

  // ── 生成计划 ─────────────────────────────────────────────────────

  const handlePlan = async () => {
    if (!configStatus?.execution_ready) {
      toastError(configStatusError ? "无法确认系统执行状态，请稍后重试" : "系统执行能力尚未就绪");
      return;
    }
    if (!companyName.trim()) {
      toastError("请填写公司名称");
      return;
    }
    if (!skillsLoaded || skillLoadError || !skillOptions.some((skill) => skill.name === selectedSkillName)) {
      toastError(skillLoadError || "可执行 Skill 尚未加载完成，请稍后重试");
      return;
    }
    setIsPlanning(true);
    try {
      const result = await planTask({
        company_name: companyName.trim(),
        demand_direction: demandDirection.trim() || "通用商机调研",
        industry: industry.trim() || null,
        region: region.trim() || null,
        business_goal: businessGoal.trim() || null,
        depth,
      });
      setPlanResult(result);
      setStep("plan");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "计划生成失败");
    } finally {
      setIsPlanning(false);
    }
  };

  // ── 确认创建 ─────────────────────────────────────────────────────

  const handleCreate = async () => {
    if (!companyName.trim()) {
      toastError("公司名称不能为空");
      return;
    }

    if (!configStatus?.execution_ready) {
      toastError(configStatusError ? "无法确认系统执行状态，请稍后重试" : "系统执行能力尚未就绪，请先完成连接验证");
      return;
    }

    setIsCreating(true);
    try {
      const targetResult = await createTargetAccount({
        input_name: companyName.trim(),
        industry: industry.trim() || undefined,
        region: region.trim() || undefined,
      });
      const targetAccount = targetResult.account || (
        targetResult.candidates.length === 1 ? targetResult.candidates[0] : null
      );
      if (!targetAccount) {
        throw new Error("发现多个同名目标企业，请先在客户管理中完成主体消歧后再创建任务");
      }
      const payload: CreateTaskPayload = {
        target_account_id: targetAccount.id,
        demand_direction: demandDirection.trim() || "通用商机调研",
        industry: industry.trim() || null,
        region: region.trim() || null,
        business_goal: businessGoal.trim() || null,
        skill_id: selectedSkillName,
        report_profile: reportProfile,
        depth,
        focus_modules: planResult?.candidate_focus || undefined,
        enable_field_agent: enableFieldAgent,
        raw_input: nlpInput.trim() || null,
      };

      const result = await createTask(payload);
      router.push(`/tasks/${result.task_id}`);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "创建任务失败");
      setIsCreating(false);
    }
  };

  // ── 返回修改 ─────────────────────────────────────────────────────

  const backToForm = () => setStep("form");
  const backToInput = () => { setStep("input"); setInterpretResult(null); };

  // ── 是否为缺失字段 ───────────────────────────────────────────────

  const isMissing = (field: string) => interpretResult?.missing_fields?.includes(field);
  const lowConfidence = interpretResult && interpretResult.confidence < 0.6;

  const inputClass = (missing: boolean) =>
    `w-full rounded-lg border bg-white px-4 py-2.5 text-base text-neutral-950 transition-all placeholder:text-neutral-400 focus:outline-none focus:ring-2 ${
      missing
        ? "border-orange-400 focus:ring-orange-400/20"
        : "border-neutral-950/20 focus:border-neutral-950 focus:ring-neutral-950/10"
    }`;

  // ── 渲染 ─────────────────────────────────────────────────────────

  return (
    <Card variant="bordered" padding="lg" className="h-fit">
      {/* 标题 */}
      <div className="mb-6">
        <p className="mb-2 text-xs font-semibold uppercase text-neutral-500">TASK LAUNCH</p>
        <h2 className="text-xl font-semibold text-neutral-950">新建调研任务</h2>
        <p className="mt-1 text-sm text-neutral-600">
          {step === "input" && "输入客户需求，系统自动解析并预填表单"}
          {step === "form" && "确认或修改解析结果，选择 Skill 和参数"}
          {step === "plan" && "确认调研计划和成本预估"}
          {step === "creating" && "正在创建任务..."}
        </p>
      </div>

      {/* ── Step 1: NLP 输入 ────────────────────────────────────── */}
      {step === "input" && (
        <div className="space-y-5">
          <div>
            <label className="mb-2 block text-sm font-medium text-neutral-700">
              客户需求描述
            </label>
            <textarea
              value={nlpInput}
              onChange={(e) => setNlpInput(e.target.value)}
              placeholder="例如：某某市政务服务中心需要升级智能客服系统，重点关注政策合规和招投标机会"
              rows={4}
              className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-3 text-base text-neutral-950 transition-all placeholder:text-neutral-400 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/10 resize-none"
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleInterpret();
              }}
            />
            <p className="mt-1.5 text-xs text-neutral-400">Ctrl+Enter 快速解析</p>
          </div>

          <div className="flex gap-3">
            <Button variant="primary" size="lg" onClick={handleInterpret} isLoading={isInterpreting}>
              {isInterpreting ? "解析中..." : "解析需求"}
            </Button>
            <Button variant="secondary" size="lg" onClick={skipInterpret}>
              手动填写
            </Button>
          </div>
        </div>
      )}

      {/* ── Step 2: 表单确认 ─────────────────────────────────────── */}
      {step === "form" && (
        <div className="space-y-5">
          {/* 置信度提示 */}
          {lowConfidence && (
            <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-3 text-sm text-yellow-700">
              系统对解析结果置信度较低（{Math.round(interpretResult.confidence * 100)}%），请确认以下字段是否正确。
            </div>
          )}

          {/* 公司名称 */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-neutral-700">
              公司名称 <span className="text-red-500">*</span>
              {isMissing("company_name") && <span className="ml-2 text-xs text-orange-600 font-normal">需要确认</span>}
            </label>
            <input type="text" value={companyName} onChange={(e) => setCompanyName(e.target.value)}
              placeholder="例如：中国移动通信集团"
              className={inputClass(!!isMissing("company_name"))} />
          </div>

          {/* 需求方向 */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-neutral-700">
              需求方向 {!demandDirection && <span className="text-xs text-neutral-400 font-normal">（可留空，表示通用调研）</span>}
            </label>
            <input type="text" value={demandDirection} onChange={(e) => setDemandDirection(e.target.value)}
              placeholder="例如：智能客服升级"
              className={inputClass(false)} />
          </div>

          {/* 行业 + 地区 */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-neutral-700">
                行业 {isMissing("industry") && <span className="text-xs text-orange-600 font-normal">需要确认</span>}
              </label>
              <input type="text" value={industry} onChange={(e) => setIndustry(e.target.value)}
                placeholder="政务、医疗、金融等"
                className={inputClass(!!isMissing("industry"))} />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-neutral-700">地区</label>
              <input type="text" value={region} onChange={(e) => setRegion(e.target.value)}
                placeholder="可选"
                className={inputClass(false)} />
            </div>
          </div>

          {/* 业务目标 */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-neutral-700">业务目标</label>
            <input type="text" value={businessGoal} onChange={(e) => setBusinessGoal(e.target.value)}
              placeholder="例如：判断是否存在售前商机"
              className={inputClass(false)} />
          </div>

          {/* Skill 下拉 */}
          <div>
            <label htmlFor="runtime-skill" className="mb-1.5 block text-sm font-medium text-neutral-700">调研 Skill</label>
            <select
              id="runtime-skill"
              value={selectedSkillName}
              onChange={(e) => setSelectedSkillName(e.target.value)}
              disabled={skillOptions.length === 0}
              className="w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-neutral-950/10 disabled:bg-neutral-100 disabled:text-neutral-500"
            >
              {skillOptions.length === 0 && <option value="pilot-opportunity">暂无可执行 Skill</option>}
              {skillOptions.map((skill) => (
                <option key={skill.name} value={skill.name}>{skill.description}</option>
              ))}
            </select>
            {skillLoadError && <p className="mt-1.5 text-xs text-red-600">{skillLoadError}</p>}
          </div>

          {/* Profile 选择器 */}
          <div>
            <label className="mb-2 block text-sm font-medium text-neutral-700">报告视角</label>
            <ProfileSelector value={reportProfile} onChange={setReportProfile} />
          </div>

          {/* Depth 选择器 */}
          <div>
            <label className="mb-2 block text-sm font-medium text-neutral-700">任务深度</label>
            <DepthSelector value={depth} onChange={setDepth} />
          </div>

          {/* Field Agent */}
          <label className="flex items-center justify-between rounded-lg border border-neutral-950/10 p-3">
            <div>
              <span className="text-sm font-medium text-neutral-800">启用网页体验背调</span>
              <p className="text-xs text-neutral-500 mt-0.5">仅访问公开网页，不会登录或提交表单</p>
            </div>
            <input type="checkbox" checked={enableFieldAgent} onChange={(e) => setEnableFieldAgent(e.target.checked)}
              className="h-4 w-4 accent-neutral-950" />
          </label>

          {/* 按钮 */}
          <div className="flex gap-3 pt-2">
            <Button variant="primary" size="lg" onClick={handlePlan} isLoading={isPlanning}
              disabled={!companyName.trim()}>
              {isPlanning ? "生成计划中..." : "生成调研计划"}
            </Button>
            <Button variant="secondary" size="lg" onClick={backToInput}>返回修改</Button>
          </div>
        </div>
      )}

      {/* ── Step 3: 计划预览 ──────────────────────────────────────── */}
      {step === "plan" && planResult && (
        <ResearchPlanPreview
          companyName={companyName}
          demandDirection={demandDirection || "通用商机调研"}
          industry={industry}
          region={region}
          reportProfile={reportProfile}
          depth={depth}
          planResult={planResult}
          enableFieldAgent={enableFieldAgent}
          onConfirm={handleCreate}
          onBack={backToForm}
          isCreating={isCreating}
        />
      )}

      {/* ── Step 4: 创建中 ────────────────────────────────────────── */}
      {step === "creating" && (
        <div className="flex items-center justify-center py-8">
          <div className="mr-3 h-6 w-6 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
          <span className="text-neutral-600">正在创建任务...</span>
        </div>
      )}
    </Card>
  );
}
