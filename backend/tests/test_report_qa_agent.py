"""WBS-32-14：报告问答必须先路由意图，并限制解释模式的数据边界。"""
from __future__ import annotations

from uuid import uuid4

from app.report_workspace.context_schema import ContextEntry, ContextManifest, ContextSource


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "content": "该结论基于已持久化的招标证据，但仍需核对截止时间。",
            "model": "fake-model",
            "provider": "fake-provider",
            "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        }


def _manifest() -> ContextManifest:
    workspace_id, thread_id, version_id = uuid4(), uuid4(), uuid4()
    source = ContextSource(domain="external", source_type="EVIDENCE", source_id="evidence-1")
    return ContextManifest(
        workspace_id=workspace_id,
        thread_id=thread_id,
        report_version_id=version_id,
        question="招标是否仍有效？",
        level0=(ContextEntry(kind="QUESTION", content="招标是否仍有效？", sources=(source,)),),
        level1=(ContextEntry(kind="EVIDENCE", content="投标截止时间尚待确认", sources=(source,)),),
        level2=(),
        level3_sources=(source,),
    )


def test_explanation_mode_only_uses_manifest_and_returns_source_references() -> None:
    from app.agents.agents.report_qa_agent import ReportQAAgent

    fake_llm = _FakeLLM()
    result = ReportQAAgent(llm_client=fake_llm, model="fake-model").answer(_manifest())

    assert result.intent == "EXPLANATION"
    assert result.requires_user_choice is False
    assert result.answer is not None
    assert result.source_ids == ("evidence-1",)
    assert len(fake_llm.calls) == 1
    assert "招标是否仍有效？" in fake_llm.calls[0]["prompt"]
    assert "搜索" not in fake_llm.calls[0]["prompt"].split("<context_manifest>", 1)[0]


def test_follow_up_and_revision_intents_do_not_call_model_and_low_confidence_requests_choice() -> None:
    from app.agents.agents.report_qa_agent import ReportQAAgent

    fake_llm = _FakeLLM()
    agent = ReportQAAgent(llm_client=fake_llm)

    follow_up = agent.answer(_manifest(), question="请继续检索今年新的招标信息")
    revision = agent.answer(_manifest(), question="请帮我处理一下", selected_intent="REPORT_REVISION")
    ambiguous = agent.answer(_manifest(), question="这件事该怎么办？")

    assert follow_up.intent == "FOLLOW_UP_RESEARCH"
    assert follow_up.answer is None
    assert revision.intent == "REPORT_REVISION"
    assert revision.answer is None
    assert ambiguous.requires_user_choice is True
    assert ambiguous.intent is None
    assert set(ambiguous.allowed_intents) == {"EXPLANATION", "FOLLOW_UP_RESEARCH", "REPORT_REVISION"}
    assert fake_llm.calls == []
