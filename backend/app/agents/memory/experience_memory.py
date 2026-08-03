"""
ExperienceMemory - 经验记忆管理

存储成功的搜索经验，供后续相似任务复用。
基于 PostgreSQL JSONB 存储，暂缓向量数据库。
"""

import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from uuid import uuid4

from sqlalchemy import desc, and_, text
from sqlalchemy.orm import Session

from app.db.models import ExperienceRecord

logger = logging.getLogger(__name__)


def _normalize_cjk(text: str) -> str:
    """提取中文汉字序列，去除标点、空白、数字、英文"""
    return re.sub(r'[^一-鿿]', '', text)


def _find_common_substrings(a: str, b: str, min_len: int = 2) -> list[str]:
    """
    找出 a 与 b 之间所有长度 >= min_len 的不重叠公共子串
    返回按长度降序排列的列表
    """
    result = []
    b_remaining = b
    for length in range(len(a), min_len - 1, -1):
        for i in range(len(a) - length + 1):
            sub = a[i:i + length]
            if sub in b_remaining:
                result.append(sub)
                b_remaining = b_remaining.replace(sub, '\0', 1)
    return result


def _direction_similarity(direction_a: str, direction_b: str) -> float:
    """计算两个需求方向的文本相似度（0~1）"""
    a = _normalize_cjk(direction_a)
    b = _normalize_cjk(direction_b)
    if not a or not b:
        return 0.0
    matches = _find_common_substrings(a, b, min_len=2)
    total_matched = sum(len(m) for m in matches)
    return min(1.0, total_matched / max(len(a), len(b)))


class ExperienceMemory:
    """
    经验记忆管理器

    负责:
    - 保存成功的搜索经验到 DB
    - 查询与当前任务相似的历史经验
    - 格式化为 Planner 可用的 prompt 片段
    - 定期清理过期经验
    """

    def __init__(self, db: Optional[Session] = None):
        self._db = db
        self._available = self._check_db()

    def _get_db(self) -> Optional[Session]:
        """获取 DB session"""
        return self._db

    def _check_db(self) -> bool:
        """检测 DB 是否可用"""
        if self._db is None:
            logger.warning("ExperienceMemory: 未提供 DB session，经验记忆功能禁用")
            return False
        try:
            self._db.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.warning(f"ExperienceMemory: DB 不可用 ({e})，经验记忆功能降级")
            return False

    def save_experience(
        self,
        task_id: str,
        dimension: str,
        company_name: str,
        demand_direction: str,
        goal: str,
        search_queries: list[str],
        strategy: str = "",
        quality_score: float = 0.0,
        iteration_count: int = 0,
        token_used: int = 0,
        meta_data: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        保存经验记录（同一 task_id + dimension 重复时 UPSERT）

        Returns:
            是否保存成功
        """
        db = self._get_db()
        if db is None:
            return False

        try:
            # 查找是否已存在
            existing = (
                db.query(ExperienceRecord)
                .filter(
                    and_(
                        ExperienceRecord.task_id == task_id,
                        ExperienceRecord.dimension == dimension,
                    )
                )
                .first()
            )

            if existing:
                existing.company_name = company_name
                existing.demand_direction = demand_direction
                existing.goal = goal
                existing.search_queries = {"queries": search_queries}
                existing.strategy = strategy
                existing.quality_score = quality_score
                existing.iteration_count = iteration_count
                existing.token_used = token_used
                existing.success = True
                existing.meta_data = meta_data or {}
                logger.info(f"ExperienceMemory: 更新经验 {dimension}/{task_id}")
            else:
                record = ExperienceRecord(
                    id=uuid4(),
                    task_id=task_id,
                    dimension=dimension,
                    company_name=company_name,
                    demand_direction=demand_direction,
                    goal=goal,
                    search_queries={"queries": search_queries},
                    strategy=strategy,
                    quality_score=quality_score,
                    iteration_count=iteration_count,
                    token_used=token_used,
                    success=True,
                    meta_data=meta_data or {},
                    created_at=datetime.now(timezone.utc),
                )
                db.add(record)
                logger.info(f"ExperienceMemory: 保存经验 {dimension}/{task_id} (score={quality_score:.2f})")

            db.commit()
            return True

        except Exception as e:
            logger.error(f"ExperienceMemory: 保存失败 - {e}")
            if db:
                db.rollback()
            return False

    def query_similar(
        self,
        dimension: str,
        company_name: str,
        demand_direction: str,
        goal: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        查询相似的历史成功经验

        匹配策略:
        1. 同维度（必须）
        2. 需求方向文本相似度排序
        3. 仅返回 success=True 的记录

        Returns:
            [{"company_name", "demand_direction", "goal", "search_queries", "strategy", "quality_score", "similarity"}, ...]
        """
        db = self._get_db()
        if db is None:
            return []

        try:
            records = (
                db.query(ExperienceRecord)
                .filter(
                    and_(
                        ExperienceRecord.dimension == dimension,
                        ExperienceRecord.success == True,
                    )
                )
                .order_by(desc(ExperienceRecord.quality_score), desc(ExperienceRecord.created_at))
                .limit(limit * 3)  # 多取一些用于排序
                .all()
            )

            if not records:
                return []

            # 按相似度排序
            scored = []
            for r in records:
                sim = _direction_similarity(
                    demand_direction, r.demand_direction
                )
                if sim > 0:
                    scored.append((sim, r))

            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[:limit]

            return [
                {
                    "company_name": r.company_name,
                    "demand_direction": r.demand_direction,
                    "goal": r.goal,
                    "search_queries": r.search_queries.get("queries", []),
                    "strategy": r.strategy,
                    "quality_score": r.quality_score,
                    "similarity": round(sim, 2),
                }
                for sim, r in top
            ]

        except Exception as e:
            logger.error(f"ExperienceMemory: 查询失败 - {e}")
            return []

    def format_for_planner(self, experiences: list[dict[str, Any]]) -> str:
        """
        将经验列表格式化为 Planner prompt 参考段落

        Args:
            experiences: query_similar 的返回结果

        Returns:
            格式化的文本（无经验时返回空字符串）
        """
        if not experiences:
            return ""

        lines = ["## 历史成功经验参考", ""]
        lines.append("以下是过去相似任务的成功搜索经验，请参考其中的搜索词和策略：")
        lines.append("")

        for i, exp in enumerate(experiences, 1):
            queries = exp.get("search_queries", [])
            strategy = exp.get("strategy", "")

            lines.append(f"### 案例 {i}（相似度：{exp.get('similarity', 0)}）")
            lines.append(f"- 公司：{exp.get('company_name', '')}")
            lines.append(f"- 需求方向：{exp.get('demand_direction', '')}")
            lines.append(f"- 挖掘目标：{exp.get('goal', '')}")
            lines.append(f"- 搜索词：{', '.join(queries)}")
            lines.append(f"- 策略：{strategy}")
            lines.append(f"- 质量评分：{exp.get('quality_score', 0):.2f}")
            lines.append("")

        lines.append("请在上述成功经验的基础上进行改进和创新，不要简单照搬。")
        return "\n".join(lines)

    def forget_old(self, max_age_days: int = 90) -> int:
        """
        清理过期经验记录

        Args:
            max_age_days: 保留天数

        Returns:
            删除的记录数
        """
        db = self._get_db()
        if db is None:
            return 0

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            deleted = (
                db.query(ExperienceRecord)
                .filter(ExperienceRecord.created_at < cutoff)
                .delete()
            )
            db.commit()
            if deleted:
                logger.info(f"ExperienceMemory: 清理 {deleted} 条过期经验（>{max_age_days}天）")
            return deleted
        except Exception as e:
            logger.error(f"ExperienceMemory: 清理失败 - {e}")
            if db:
                db.rollback()
            return 0
