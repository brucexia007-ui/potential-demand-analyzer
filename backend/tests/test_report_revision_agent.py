"""报告修订智能体必须输出可验证操作，且不得引用上下文外来源。"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.report_workspace.context_schema import ContextEntry, ContextManifest, ContextSource


class _FakeRevisionLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "content": json.dumps(self.payload, ensure_ascii=False),
            "model": "fake-revision-model",
            "provider": "fake-provider",
            "usage": {"total_tokens": 30},
        }


def _manifest() -> ContextManifest:
    source = ContextSource(domain="external", source_type="EVIDENCE", source_id="evidence-1")
    return ContextManifest(
        workspace_id=uuid4(),
        thread_id=uuid4(),
        report_version_id=uuid4(),
        question="补充风险说明",
        level0=(ContextEntry(kind="QUESTION", content="补充风险说明", sources=(source,)),),
        level1=(ContextEntry(kind="EVIDENCE", content="合同到期时间尚待确认", sources=(source,)),),
        level2=(),
        level3_sources=(source,),
    )


def test_revision_agent_applies_section_operations_without_rewriting_other_sections() -> None:
    from app.agents.agents.report_revision_agent import ReportRevisionAgent

    llm = _FakeRevisionLLM({
        "summary": "把合同到期风险标记为待确认",
        "operations": [{
            "action": "REPLACE_SECTION",
            "target_heading": "## 风险",
            "content_md": "## 风险\n\n合同到期时间待确认。【来源：EVIDENCE/evidence-1】",
        }],
        "source_ids": ["evidence-1"],
    })
    result = ReportRevisionAgent(llm_client=llm).propose(
        _manifest(),
        base_content_md="# 报告\n\n## 结论\n\n原结论\n\n## 风险\n\n原风险\n\n## 行动\n\n原行动",
        revision_request="补充合同到期风险，但不要改动其他章节",
    )

    assert "## 结论\n\n原结论" in result.proposed_content_md
    assert "合同到期时间待确认" in result.proposed_content_md
    assert "## 行动\n\n原行动" in result.proposed_content_md
    assert result.source_ids == ("evidence-1",)
    assert len(llm.calls) == 1


def test_revision_agent_rejects_unknown_sources_and_missing_target_sections() -> None:
    from app.agents.agents.report_revision_agent import ReportRevisionAgent

    unknown_source = _FakeRevisionLLM({
        "summary": "非法来源",
        "operations": [{"action": "APPEND_SECTION", "target_heading": None, "content_md": "## 新章节\n内容"}],
        "source_ids": ["not-in-context"],
    })
    with pytest.raises(ValueError, match="上下文外来源"):
        ReportRevisionAgent(llm_client=unknown_source).propose(
            _manifest(), base_content_md="# 报告", revision_request="新增章节",
        )

    missing_target = _FakeRevisionLLM({
        "summary": "目标不存在",
        "operations": [{"action": "REPLACE_SECTION", "target_heading": "## 不存在", "content_md": "## 不存在\n内容"}],
        "source_ids": [],
    })
    with pytest.raises(ValueError, match="目标章节不存在"):
        ReportRevisionAgent(llm_client=missing_target).propose(
            _manifest(), base_content_md="# 报告\n\n## 风险\n内容", revision_request="修改章节",
        )
