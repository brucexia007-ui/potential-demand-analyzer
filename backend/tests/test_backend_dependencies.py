from pathlib import Path
import re


REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def test_production_dependencies_are_exactly_pinned() -> None:
    lines = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert lines
    assert all(re.search(r"==[^,;\s]+$", line) for line in lines), (
        "生产依赖必须使用单一精确版本；范围版本会使同一发布在不同时间解析出不同制品"
    )


def test_production_dependencies_do_not_include_test_frameworks() -> None:
    package_names = {
        re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip().lower()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "pytest" not in package_names
    assert "pytest-asyncio" not in package_names
    assert "pytest-cov" not in package_names
