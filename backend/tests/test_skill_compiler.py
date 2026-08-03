"""WBS-33-18：标准 SKILL.md 必须编译为可执行但非代码化的研究契约。"""
from __future__ import annotations

import pytest


VALID_SKILL = "\n".join((
    "---", "name: pilot-opportunity", "description: 面向售前的客户商机研究", "metadata:", "  version: \"1\"", "---", "",
    "## Triggers", "- 企业名称与研究方向已确认", "", "## Questions", "- 客户当前具备哪些能力？", "- 是否存在当前采购或续约窗口？", "",
    "## Sources", "- 官方招标公告", "- 客户官网", "", "## Budget", "max_input_tokens: 20000", "max_external_calls: 12", "",
    "## Stop Conditions", "- 发现采购已完成且没有扩容或替换证据", "",
    "## Output Fields", "- event_type", "- event_date", "- target_entity", "",
    "## Quality Thresholds", "min_overall_score: 0.75", "min_field_coverage: 1.0",
    "min_evidence_count: 3", "min_distinct_domains: 2", "max_evidence_age_days: 730", "",
    "## Report Structure", "- 商机裁决", "- 证据与反证",
))


def test_compiler_builds_structured_contract_from_standard_skill_markdown() -> None:
    from app.skills.compiler import SkillCompiler

    compiled = SkillCompiler().compile(VALID_SKILL)

    assert compiled.name == "pilot-opportunity"
    assert compiled.version == 1
    assert compiled.execution_phase == "research"
    assert compiled.questions == ("客户当前具备哪些能力？", "是否存在当前采购或续约窗口？")
    assert compiled.sources == ("官方招标公告", "客户官网")
    assert compiled.budget["max_input_tokens"] == 20000
    assert compiled.output_fields == ("event_type", "event_date", "target_entity")
    assert compiled.quality_thresholds == {
        "min_overall_score": 0.75,
        "min_field_coverage": 1.0,
        "min_evidence_count": 3,
        "min_distinct_domains": 2,
        "max_evidence_age_days": 730,
    }
    assert compiled.report_sections == ("商机裁决", "证据与反证")


def test_compiler_rejects_missing_required_metadata_or_executable_code() -> None:
    from app.skills.compiler import SkillCompiler

    with pytest.raises(ValueError, match="name"):
        SkillCompiler().compile("\n".join(("---", "description: 缺少名称", "---", "")))
    with pytest.raises(ValueError, match="可执行代码"):
        SkillCompiler().compile(VALID_SKILL + "\n```python\nprint('unsafe')\n```\n")


def test_compiler_rejects_nonstandard_top_level_frontmatter_fields() -> None:
    from app.skills.compiler import SkillCompiler

    invalid = VALID_SKILL.replace('metadata:\n  version: "1"', "version: 1")
    with pytest.raises(ValueError, match="不支持的 Frontmatter 字段.*version"):
        SkillCompiler().compile(invalid)


def test_compiler_requires_research_questions_and_sources() -> None:
    from app.skills.compiler import SkillCompiler

    with pytest.raises(ValueError, match="Questions"):
        SkillCompiler().compile("\n".join(("---", "name: x", "description: y", "---", "## Sources", "- 官方来源")))
    with pytest.raises(ValueError, match="Sources"):
        SkillCompiler().compile("\n".join(("---", "name: x", "description: y", "---", "## Questions", "- 问题")))


def test_compiler_accepts_only_declared_execution_phases() -> None:
    from app.skills.compiler import SkillCompiler

    evaluation = VALID_SKILL.replace(
        '  version: "1"', '  version: "1"\n  execution_phase: evaluation'
    )
    assert SkillCompiler().compile(evaluation).execution_phase == "evaluation"
    invalid = evaluation.replace("execution_phase: evaluation", "execution_phase: external-search")
    with pytest.raises(ValueError, match="execution_phase"):
        SkillCompiler().compile(invalid)


@pytest.mark.parametrize(
    ("line", "message"),
    (
        ("unknown_threshold: 1", "不支持的质量阈值"),
        ("min_overall_score: 1.1", "0 到 1"),
        ("min_evidence_count: 0", "大于 0"),
        ("max_evidence_age_days: -1", "不能为负数"),
    ),
)
def test_compiler_rejects_invalid_quality_thresholds(line: str, message: str) -> None:
    from app.skills.compiler import SkillCompiler

    start = VALID_SKILL.index("min_overall_score: 0.75")
    end = VALID_SKILL.index("\n\n## Report Structure")
    invalid = VALID_SKILL[:start] + line + VALID_SKILL[end:]

    with pytest.raises(ValueError, match=message):
        SkillCompiler().compile(invalid)


def test_compiler_rejects_invalid_or_duplicate_output_fields() -> None:
    from app.skills.compiler import SkillCompiler

    invalid_name = VALID_SKILL.replace("- event_type", "- Event Type")
    with pytest.raises(ValueError, match="输出字段"):
        SkillCompiler().compile(invalid_name)

    duplicate = VALID_SKILL.replace("- event_date", "- event_type")
    with pytest.raises(ValueError, match="重复"):
        SkillCompiler().compile(duplicate)


def test_compiler_accepts_bounded_tools_domains_and_dependency_conditions() -> None:
    from app.skills.compiler import SkillCompiler

    markdown = VALID_SKILL.replace(
        '  version: "1"',
        '  version: "1"\n'
        "  allowed_tools: [external_search, customer_private_retrieval]\n"
        "  data_domains: [external, customer_private]\n"
        "  dependency_conditions:\n"
        "    child-skill:\n"
        "      all:\n"
        "        - field: research_mode\n"
        "          operator: EQ\n"
        "          value: OPPORTUNITY_DISCOVERY",
    ) + "\n## Dependencies\n- child-skill@1\n"

    compiled = SkillCompiler().compile(markdown)

    assert compiled.allowed_tools == ("external_search", "customer_private_retrieval")
    assert compiled.data_domains == ("external", "customer_private")
    assert compiled.dependency_conditions["child-skill"]["all"][0]["operator"] == "EQ"


def test_compiler_rejects_unapproved_tool_domain_and_condition_language() -> None:
    from app.skills.compiler import SkillCompiler

    missing_domain = VALID_SKILL.replace(
        '  version: "1"',
        '  version: "1"\n  allowed_tools: [customer_private_retrieval]\n  data_domains: [external]',
    )
    with pytest.raises(ValueError, match="必须声明数据域"):
        SkillCompiler().compile(missing_domain)

    executable_condition = VALID_SKILL.replace(
        '  version: "1"',
        '  version: "1"\n'
        "  dependency_conditions:\n"
        "    child-skill:\n"
        "      all:\n"
        "        - field: __import__\n"
        "          operator: EQ\n"
        "          value: os",
    ) + "\n## Dependencies\n- child-skill@1\n"
    with pytest.raises(ValueError, match="未批准字段或操作符"):
        SkillCompiler().compile(executable_condition)
def test_compiler_accepts_field_agent_as_external_read_only_tool() -> None:
    from app.skills.compiler import SkillCompiler

    markdown = VALID_SKILL.replace(
        '  version: "1"',
        '  version: "1"\n'
        "  allowed_tools: [external_search, external_fetch, field_agent]\n"
        "  data_domains: [external]",
    )

    compiled = SkillCompiler().compile(markdown)

    assert "field_agent" in compiled.allowed_tools
    assert compiled.data_domains == ("external",)
