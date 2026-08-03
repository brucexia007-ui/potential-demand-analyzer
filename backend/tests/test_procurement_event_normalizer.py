from datetime import datetime, timezone

from app.evidence.procurement_event_normalizer import normalize_procurement_fields


def test_extracts_project_code_stage_supplier_amount_and_dates() -> None:
    fields = normalize_procurement_fields(
        title="智能客服系统采购项目中标公告",
        content=(
            "项目编号：CPIC-2026-CC-001。"
            "中标供应商：示例科技股份有限公司。"
            "中标金额：128.6万元。"
            "合同服务期限至2027年12月31日。"
        ),
        published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    assert fields["project_code"] == "CPIC-2026-CC-001"
    assert fields["event_stage"] == "AWARDED"
    assert fields["supplier"] == "示例科技股份有限公司"
    assert fields["amount_yuan"] == 1_286_000
    assert fields["contract_end_date"] == "2027-12-31"
    assert fields["event_date"] == "2026-07-20"
    assert fields["project_key"] == "code:cpic-2026-cc-001"


def test_same_project_title_forms_one_project_key_across_event_pages() -> None:
    announced = normalize_procurement_fields(
        title="智能客服系统建设项目采购公告",
        content="投标截止时间：2026年8月15日",
        published_at=None,
    )
    awarded = normalize_procurement_fields(
        title="智能客服系统建设项目中标结果公告",
        content="",
        published_at=None,
    )

    assert announced["event_stage"] == "TENDERING"
    assert announced["deadline_date"] == "2026-08-15"
    assert awarded["event_stage"] == "AWARDED"
    assert announced["project_key"] == awarded["project_key"]
