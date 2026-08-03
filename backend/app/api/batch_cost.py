"""WBS-9.7: 批处理成本估算与 Dry Run 采样服务

提供：
1. calculate_sample_score() — 对导入行评分
2. estimate_batch_cost() — 根据样本外推总成本
3. select_samples() — 选择 Dry Run 样本
"""
from __future__ import annotations

import re


def calculate_sample_score(
    company_name: str,
    demand_direction: str,
    skill_type: str = "bidding",
) -> float:
    """计算导入行的采样评分（来自 litcoffee v3 算法）

    sample_score = 字段完整度 × 0.4 + 需求明确度 × 0.3 + Skill匹配度 × 0.2 + 数据质量 × 0.1 - 歧义惩罚

    评分越高，代表该行越适合作为 Dry Run 样本。
    """
    score = 0.0

    # 字段完整度 (0.4)
    completeness = 0.0
    if company_name and demand_direction:
        completeness = 1.0
    elif company_name or demand_direction:
        completeness = 0.5
    score += completeness * 0.4

    # 需求明确度 (0.3)
    if demand_direction:
        if len(demand_direction) > 5:
            clarity = 1.0
        else:
            clarity = 0.5
    else:
        clarity = 0.0
    score += clarity * 0.3

    # Skill 匹配度 (0.2)
    # 内置 skill_type 都有效
    known_skills = {"bidding", "policy", "capacity", "full"}
    skill_match = 1.0 if skill_type in known_skills else 0.5
    score += skill_match * 0.2

    # 数据质量 (0.1)
    quality = 1.0
    # 检测乱码/特殊字符
    if re.search(r"[^一-鿿\w\s\-\.\(\)（）]", company_name + demand_direction):
        quality = 0.5
    score += quality * 0.1

    # 歧义惩罚
    if len(company_name) < 2:
        score -= 0.3

    return max(0.0, round(score, 4))


def select_samples(
    rows: list[dict],
    max_samples: int = 2,
) -> list[dict]:
    """从导入行中选择 Dry Run 样本

    按 sample_score 降序排列，取前 max_samples 行。
    每行添加 sample_score 和 rank 字段。

    Args:
        rows: [{"company_name": str, "demand_direction": str, ...}, ...]
        max_samples: 最大样本数

    Returns:
        带评分和排名的行列表（最多 max_samples 条）
    """
    scored = []
    for idx, row in enumerate(rows):
        score = calculate_sample_score(
            company_name=row.get("company_name", ""),
            demand_direction=row.get("demand_direction", ""),
            skill_type=row.get("skill_type", "bidding"),
        )
        scored.append({
            "row_index": idx,
            **row,
            "sample_score": score,
        })

    # 按评分降序
    scored.sort(key=lambda r: r["sample_score"], reverse=True)

    # 标注排名
    for rank, item in enumerate(scored[:max_samples], 1):
        item["rank"] = rank

    return scored[:max_samples]


def estimate_batch_cost(
    sample_results: list[dict],
    total_rows: int,
) -> dict:
    """根据 Dry Run 规划样本外推批次资源预算。

    Args:
        sample_results: 每个样本的结果 [{"tokens_used": int, "time_seconds": float, "evidence_count": int}, ...]
        total_rows: 批次总行数

    Returns:
        {
            "estimated_total_tokens": int,
            "estimated_total_time_minutes": float,
            "monetary_cost": {"status": "UNAVAILABLE", "amount": None, ...},
            "total_rows": int,
            "sample_count": int,
            "confidence": str,  # "low" | "medium" | "high"
        }
    """
    if not sample_results:
        return {
            "estimated_total_tokens": 0,
            "estimated_total_time_minutes": 0.0,
            "monetary_cost": {
                "status": "UNAVAILABLE",
                "amount": None,
                "currency": None,
                "reason": "当前模型与搜索供应商没有统一价目表，禁止伪造金额估算。",
            },
            "total_rows": total_rows,
            "sample_count": 0,
            "confidence": "low",
            "estimate_basis": "Skill 声明预算的确定性规划外推，Dry Run 不调用外部 Provider。",
        }

    sample_count = len(sample_results)

    # 汇总样本指标
    total_tokens = sum(r.get("tokens_used", 0) for r in sample_results)
    total_time = sum(r.get("time_seconds", 0) for r in sample_results)

    # 外推
    avg_tokens = total_tokens / sample_count if sample_count > 0 else 0
    avg_time = total_time / sample_count if sample_count > 0 else 0

    estimated_tokens = int(avg_tokens * total_rows)
    estimated_time_minutes = round((avg_time * total_rows) / 60, 1)

    # 置信度
    if sample_count >= 3:
        confidence = "high"
    elif sample_count >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "estimated_total_tokens": estimated_tokens,
        "estimated_total_time_minutes": estimated_time_minutes,
        "monetary_cost": {
            "status": "UNAVAILABLE",
            "amount": None,
            "currency": None,
            "reason": "当前模型与搜索供应商没有统一价目表，禁止伪造金额估算。",
        },
        "total_rows": total_rows,
        "sample_count": sample_count,
        "confidence": confidence,
        "estimate_basis": "Skill 声明预算的确定性规划外推，Dry Run 不调用外部 Provider。",
    }
