"""WBS-OIG-05：采购性质决定后续窗口与生命周期规则。"""
from __future__ import annotations


def test_procurement_classifier_distinguishes_maintenance_subscription_and_one_time_build() -> None:
    from app.opportunities.procurement_classifier import ProcurementClassifier, ProcurementTextInput

    classifier = ProcurementClassifier()
    maintenance = classifier.classify(ProcurementTextInput(
        title="智能客服平台三年运维服务采购项目",
        content="提供驻场技术支持、故障处理和年度系统维护服务。",
    ))
    subscription = classifier.classify(ProcurementTextInput(
        title="大模型知识库 SaaS 订阅服务",
        content="按年度订阅并按账号数量计费。",
    ))
    build = classifier.classify(ProcurementTextInput(
        title="客服平台建设项目",
        content="完成软件开发、部署实施和验收交付。",
    ))

    assert maintenance.nature == "MAINTENANCE"
    assert subscription.nature == "SUBSCRIPTION"
    assert build.nature == "ONE_TIME_BUILD"


def test_procurement_classifier_returns_mixed_or_unknown_without_forcing_a_window() -> None:
    from app.opportunities.procurement_classifier import ProcurementClassifier, ProcurementTextInput

    classifier = ProcurementClassifier()
    mixed = classifier.classify(ProcurementTextInput(
        title="客服平台建设及三年维保服务",
        content="包含软件建设、实施交付和后续运维。",
    ))
    unknown = classifier.classify(ProcurementTextInput(title="数字化项目", content="项目相关事项。"))

    assert mixed.nature == "MIXED"
    assert mixed.confidence == 1.0
    assert unknown.nature == "UNKNOWN"
    assert unknown.confidence == 0.0
