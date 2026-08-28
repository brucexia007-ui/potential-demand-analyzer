from pathlib import Path
import re


REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def _production_dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for raw_line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^,;\s]+)", line)
        assert match is not None, f"无法识别生产依赖声明: {line}"
        versions[match.group(1).lower()] = match.group(2)
    return versions


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


def test_auth_and_upload_dependencies_use_audited_versions() -> None:
    versions = _production_dependency_versions()

    assert "litellm" not in versions, "项目未导入 LiteLLM SDK，不应保留无效攻击面"
    assert versions["pyjwt"] == "2.13.0"
    assert "python-jose" not in versions, "python-jose 会传递引入无修复版本的 ecdsa"
    assert versions["python-multipart"] == "0.0.32"
    assert versions["python-dotenv"] == "1.2.2"


def test_pdf_dependency_uses_audited_version() -> None:
    versions = _production_dependency_versions()

    assert versions["pypdf"] == "6.16.2"


def test_langgraph_dependency_uses_audited_version() -> None:
    versions = _production_dependency_versions()

    assert versions["langgraph"] == "1.2.9"


def test_web_framework_dependencies_use_audited_versions() -> None:
    versions = _production_dependency_versions()

    assert versions["fastapi"] == "0.139.2"
    assert versions["starlette"] == "1.3.1"
    assert versions["prometheus_fastapi_instrumentator"] == "8.0.2"
