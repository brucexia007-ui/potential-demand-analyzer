"""在受限子进程中解析不可信 PDF。"""
from __future__ import annotations

import json
import os
import resource
import sys
from io import BytesIO


PDF_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
PDF_CPU_SOFT_LIMIT_SECONDS = 15
PDF_CPU_HARD_LIMIT_SECONDS = 16


def _fail(code: str) -> int:
    sys.stderr.write(code)
    return 2


def main() -> int:
    resource.setrlimit(
        resource.RLIMIT_AS,
        (PDF_MEMORY_LIMIT_BYTES, PDF_MEMORY_LIMIT_BYTES),
    )
    resource.setrlimit(
        resource.RLIMIT_CPU,
        (PDF_CPU_SOFT_LIMIT_SECONDS, PDF_CPU_HARD_LIMIT_SECONDS),
    )

    max_bytes = int(os.environ["PDF_PARSE_MAX_BYTES"])
    max_pages = int(os.environ["PDF_PARSE_MAX_PAGES"])
    max_chars = int(os.environ["PDF_PARSE_MAX_CHARS"])
    content = sys.stdin.buffer.read(max_bytes + 1)
    if len(content) > max_bytes:
        return _fail("PDF_SIZE_LIMIT")

    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        if len(reader.pages) > max_pages:
            return _fail("PDF_PAGE_LIMIT")

        extracted_chars = 0
        segments: list[dict[str, str]] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            extracted_chars += len(text)
            if extracted_chars > max_chars:
                return _fail("PDF_TEXT_LIMIT")
            segments.append({"content": text, "page_ref": f"page:{index}"})
    except MemoryError:
        return _fail("PDF_MEMORY_LIMIT")
    except Exception:
        return _fail("PDF_PARSE_ERROR")

    sys.stdout.write(json.dumps(segments, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
