"""WBS-OIG-05：基于可解释关键词的采购性质分类。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProcurementNature = Literal[
    "ONE_TIME_BUILD",
    "SOFTWARE_LICENSE",
    "SUBSCRIPTION",
    "MAINTENANCE",
    "OPERATIONS",
    "FRAMEWORK",
    "STAFFING",
    "CONSULTING",
    "SECURITY_SERVICE",
    "MIXED",
    "UNKNOWN",
]


@dataclass(frozen=True)
class ProcurementTextInput:
    """仅承载已取得的标题与正文，不补全或推断缺失事实。"""

    title: str
    content: str

    def __post_init__(self) -> None:
        if not self.title.strip() and not self.content.strip():
            raise ValueError("title 与 content 不能同时为空")


@dataclass(frozen=True)
class ProcurementClassification:
    nature: ProcurementNature
    confidence: float
    reasons: tuple[str, ...]


_NATURE_SIGNALS: tuple[tuple[ProcurementNature, tuple[str, ...]], ...] = (
    ("FRAMEWORK", ("框架协议", "框架采购", "入围")),
    ("SUBSCRIPTION", ("saas", "订阅", "按年付费", "按年度付费")),
    ("SOFTWARE_LICENSE", ("软件许可", "软件授权", "license", "licence")),
    ("SECURITY_SERVICE", ("攻防", "渗透测试", "安全测评", "安全监测", "安全运营")),
    ("STAFFING", ("人力外包", "劳务派遣", "驻场人员", "人员服务")),
    ("CONSULTING", ("咨询服务", "咨询规划", "评估服务", "顶层设计")),
    ("OPERATIONS", ("运营服务", "运营托管", "内容运营")),
    ("MAINTENANCE", ("维保", "运维", "系统维护", "故障处理", "技术支持", "驻场技术支持")),
    ("ONE_TIME_BUILD", ("建设项目", "软件开发", "部署实施", "实施交付", "验收交付")),
)
_BUILD_SIGNALS = ("建设", "开发", "部署", "实施", "交付")
_MAINTENANCE_SIGNALS = ("维保", "维护", "运维", "技术支持")


class ProcurementClassifier:
    """采购性质是后续规则的输入，不是商机成立、金额或可赢性的判断。"""

    def classify(self, source: ProcurementTextInput) -> ProcurementClassification:
        text = f"{source.title}\n{source.content}".lower()
        build_hits = self._matching_signals(text, _BUILD_SIGNALS)
        maintenance_hits = self._matching_signals(text, _MAINTENANCE_SIGNALS)
        if build_hits and maintenance_hits:
            return ProcurementClassification(
                nature="MIXED",
                confidence=1.0,
                reasons=(
                    f"同时识别到建设/交付信号：{','.join(build_hits)}",
                    f"以及维护/运营信号：{','.join(maintenance_hits)}",
                ),
            )

        for nature, signals in _NATURE_SIGNALS:
            hits = self._matching_signals(text, signals)
            if hits:
                return ProcurementClassification(
                    nature=nature,
                    confidence=1.0,
                    reasons=(f"识别到{nature}采购信号：{','.join(hits)}",),
                )
        return ProcurementClassification(
            nature="UNKNOWN",
            confidence=0.0,
            reasons=("未发现足以确定采购性质的明确文本信号，不能强制归类。",),
        )

    @staticmethod
    def _matching_signals(text: str, signals: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(signal for signal in signals if signal in text)
