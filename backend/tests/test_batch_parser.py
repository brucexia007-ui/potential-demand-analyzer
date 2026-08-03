"""batch_parser 测试：CSV 解析、列名匹配、边界情况"""

import pytest
from app.api.batch_parser import parse_csv_to_rows, CsvParseError, _normalize_headers


# ── _normalize_headers 单元测试 ──────────────────────────────────────

class TestNormalizeHeaders:
    def test_english_columns(self):
        mapping = _normalize_headers(["company_name", "demand_direction"])
        assert mapping == {"company_name": "company_name", "demand_direction": "demand_direction"}

    def test_chinese_columns(self):
        mapping = _normalize_headers(["公司名称", "需求方向"])
        assert mapping == {"company_name": "公司名称", "demand_direction": "需求方向"}

    def test_chinese_alias_columns(self):
        mapping = _normalize_headers(["企业名称", "查询方向"])
        assert mapping == {"company_name": "企业名称", "demand_direction": "查询方向"}

    def test_mixed_columns(self):
        mapping = _normalize_headers(["company_name", "需求方向"])
        assert mapping == {"company_name": "company_name", "demand_direction": "需求方向"}

    def test_extra_columns(self):
        mapping = _normalize_headers(["企业", "需求", "备注", "优先级"])
        assert mapping == {"company_name": "企业", "demand_direction": "需求"}

    def test_missing_company_name_raises(self):
        with pytest.raises(CsvParseError, match="企业名称"):
            _normalize_headers(["需求方向", "备注"])

    def test_missing_demand_direction_raises(self):
        with pytest.raises(CsvParseError, match="需求方向"):
            _normalize_headers(["企业名称", "备注"])


# ── parse_csv_to_rows 集成测试 ───────────────────────────────────────

class TestParseCsvToRows:
    def test_basic_csv(self):
        csv = b"company_name,demand_direction\nApple,iPhone\nGoogle,Cloud\n"
        result = parse_csv_to_rows(csv, "test.csv")
        assert result["source_row_count"] == 2
        assert result["candidate_rows"] == [
            {"source_row_index": 2, "company_name": "Apple", "demand_direction": "iPhone"},
            {"source_row_index": 3, "company_name": "Google", "demand_direction": "Cloud"},
        ]

    def test_chinese_column_names(self):
        csv = "公司名称,需求方向\n华为,云计算\n阿里巴巴,数据中台\n".encode("utf-8")
        result = parse_csv_to_rows(csv, "test.csv")
        assert result["source_row_count"] == 2
        assert result["candidate_rows"][0]["company_name"] == "华为"

    def test_utf8_bom(self):
        csv = "﻿company_name,demand_direction\n测试,分析\n".encode("utf-8-sig")
        result = parse_csv_to_rows(csv, "test.csv")
        assert result["source_row_count"] == 1
        assert result["candidate_rows"][0]["company_name"] == "测试"

    def test_gbk_encoding(self):
        csv = "公司名称,需求方向\n字节跳动,AI平台\n".encode("gbk")
        result = parse_csv_to_rows(csv, "test.csv")
        assert result["source_row_count"] == 1
        assert result["candidate_rows"][0]["company_name"] == "字节跳动"

    def test_semicolon_delimiter(self):
        csv = b"company_name;demand_direction\nApple;iPhone\nGoogle;Cloud\n"
        result = parse_csv_to_rows(csv, "test.csv")
        assert result["source_row_count"] == 2

    def test_preview_limited_to_5(self):
        rows_data = "company_name,demand_direction\n"
        for i in range(10):
            rows_data += f"Company{i},Demand{i}\n"
        result = parse_csv_to_rows(rows_data.encode("utf-8"), "test.csv")
        assert result["source_row_count"] == 10
        assert len(result["preview_candidates"]) == 5

    def test_empty_rows_skipped(self):
        csv = b"company_name,demand_direction\n\nApple,iPhone\n\n\nGoogle,Cloud\n"
        result = parse_csv_to_rows(csv, "test.csv")
        assert result["source_row_count"] == 2

    def test_whitespace_trimmed(self):
        csv = b"company_name,demand_direction\n  Apple  ,  iPhone  \n"
        result = parse_csv_to_rows(csv, "test.csv")
        assert result["candidate_rows"][0] == {
            "source_row_index": 2,
            "company_name": "Apple",
            "demand_direction": "iPhone",
        }

    def test_empty_file_raises(self):
        with pytest.raises(CsvParseError, match="表头行"):
            parse_csv_to_rows(b"", "test.csv")

    def test_header_only_raises(self):
        with pytest.raises(CsvParseError):
            parse_csv_to_rows(b"company_name,demand_direction\n", "test.csv")

    def test_no_valid_rows_raises(self):
        result = parse_csv_to_rows(b"company_name,demand_direction\n,missing company\nmissing demand,\n", "test.csv")
        assert result["source_row_count"] == 2
        assert [row["source_row_index"] for row in result["candidate_rows"]] == [2, 3]
        assert result["candidate_rows"][0]["company_name"] is None
        assert result["candidate_rows"][1]["demand_direction"] is None

    def test_source_indexes_survive_invalid_rows(self):
        csv = "企业名称,需求方向,行业,地区\n甲公司,数据治理,金融,上海\n,缺少企业,政务,北京\n乙公司,,制造,苏州\n".encode()
        result = parse_csv_to_rows(csv, "test.csv")

        assert result["source_row_count"] == 3
        assert [row["source_row_index"] for row in result["candidate_rows"]] == [2, 3, 4]
        assert result["candidate_rows"][0]["industry"] == "金融"
        assert result["candidate_rows"][0]["region"] == "上海"

    def test_file_too_large_raises(self):
        with pytest.raises(CsvParseError, match="过大"):
            parse_csv_to_rows(b"x" * (6 * 1024 * 1024), "test.csv")


# ── 边缘情况 ──────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_column_name_with_spaces(self):
        mapping = _normalize_headers(["  公司名称  ", " 需求方向 "])
        assert mapping == {"company_name": "公司名称", "demand_direction": "需求方向"}

    def test_duplicate_column_uses_first(self):
        mapping = _normalize_headers(["company_name", "demand_direction", "company_name"])
        # 第二个 company_name 被忽略，demand_direction 正常
        assert mapping == {"company_name": "company_name", "demand_direction": "demand_direction"}

    def test_single_column_raises(self):
        with pytest.raises(CsvParseError):
            _normalize_headers(["公司名称"])

    def test_tab_delimiter(self):
        csv = b"company_name\tdemand_direction\nApple\tiPhone\n"
        result = parse_csv_to_rows(csv, "test.csv")
        assert result["source_row_count"] == 1
