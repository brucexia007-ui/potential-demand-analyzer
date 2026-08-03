"""PDF 导出必须包含可提取的中文文本层（ToUnicode CMap）。"""
from pypdf import PdfReader
import io

from app.tools.export_client import ExportClient


def _export() -> bytes:
    return ExportClient().export_to_pdf(
        "# 测试标题\n\n这是一段中文内容，用于验证 PDF 文本层。\n\n- 条目一\n- 条目二\n",
        title="测试报告",
    )


def test_pdf_text_is_extractable() -> None:
    text = PdfReader(io.BytesIO(_export())).pages[0].extract_text()
    assert "测试标题" in text
    assert "中文内容" in text


def test_pdf_size_stays_reasonable() -> None:
    """不得为文本层付出全字体内嵌的体积代价（< 2MB）。"""
    pdf_bytes = _export()
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) < 2_000_000
