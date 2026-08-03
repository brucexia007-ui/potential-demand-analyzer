"""
EvidenceTrust 证据可信底座

为证据提供文件快照、来源可信度评分和引用校验能力。

模块：
- snapshot_service: 快照文件存储与 TTL 清理
- source_reliability: 来源可信度评分（S/A/B/C/UNKNOWN）
- claim_reference_validator: 报告结论引用校验的唯一入口
"""
from app.evidence.snapshot_service import SnapshotService, SnapshotMeta
from app.evidence.source_reliability import ReliabilityTier, score_source_reliability

__all__ = [
    "SnapshotService",
    "SnapshotMeta",
    "ReliabilityTier",
    "score_source_reliability",
]
