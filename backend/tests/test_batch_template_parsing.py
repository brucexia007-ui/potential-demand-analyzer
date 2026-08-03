"""模板解析必须识别模式/版本，并允许自动线索发现只填写企业名称。"""
from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from app.api.batch_parser import CsvParseError, parse_csv_to_rows, parse_excel_to_rows
from app.api.batch_template_service import BatchTemplateService


def test_discovery_csv_requires_only_company_and_skips_packaged_example() -> None:
    generated = BatchTemplateService().generate(template_id="opportunity_discovery", file_format="csv")
    content = generated.content + "目标制造集团,,,,,\n".encode("utf-8")

    result = parse_csv_to_rows(content, generated.filename)

    assert result["template_id"] == "opportunity_discovery"
    assert result["template_version"] == 1
    assert result["source_row_count"] == 1
    assert result["candidate_rows"][0] == {
        "source_row_index": 3,
        "company_name": "目标制造集团",
        "demand_direction": "自动发现潜在需求与商机线索",
    }


def test_discovery_xlsx_reads_optional_disambiguation_without_making_it_required() -> None:
    generated = BatchTemplateService().generate(template_id="opportunity_discovery", file_format="xlsx")
    workbook = load_workbook(io.BytesIO(generated.content))
    sheet = workbook["导入数据"]
    sheet.append(["目标科技公司", "https://target.example", "91310000TEST", "上海", "软件", ""])
    output = io.BytesIO()
    workbook.save(output)

    result = parse_excel_to_rows(output.getvalue(), generated.filename)

    assert result["template_id"] == "opportunity_discovery"
    assert result["candidate_rows"][0]["industry"] == "软件"
    assert result["candidate_rows"][0]["region"] == "上海"
    assert result["candidate_rows"][0]["disambiguation"] == {
        "official_website": "https://target.example",
        "unified_social_credit_code": "91310000TEST",
    }


def test_standard_template_still_requires_demand_and_rejects_unknown_version() -> None:
    missing_demand = (
        "__kanyikan_template__,standard_research,1\n"
        "企业名称,官网\n"
        "目标企业,https://target.example\n"
    ).encode("utf-8")
    with pytest.raises(CsvParseError, match="需求方向"):
        parse_csv_to_rows(missing_demand, "standard.csv")

    unknown_version = (
        "__kanyikan_template__,opportunity_discovery,99\n"
        "企业名称\n"
        "目标企业\n"
    ).encode("utf-8")
    with pytest.raises(CsvParseError, match="版本不受支持"):
        parse_csv_to_rows(unknown_version, "future.csv")
