"""外部 Skill 获取只生成固定、只读、安全的 UTF-8 文本快照。"""
from __future__ import annotations

import stat
import zipfile
from io import BytesIO

import pytest

from app.security.skill_package_guard import SkillPackageGuard
from app.skills.source_fetcher import SkillSourceFetcher


def _zip(files: dict[str, bytes | str], *, symlink: str | None = None) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path, content in files.items():
            package.writestr(path, content.encode() if isinstance(content, str) else content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(info, "SKILL.md")
    return output.getvalue()


def test_offline_package_unwraps_single_root_and_never_extracts() -> None:
    archive = _zip({
        "sample/SKILL.md": "---\nname: sample\ndescription: test\n---\n# Workflow",
        "sample/references/rules.md": "只使用公开证据",
        "sample/LICENSE": "MIT",
    })

    snapshot = SkillSourceFetcher().from_offline_zip(archive)

    assert snapshot.source_type == "OFFLINE_ARCHIVE"
    assert snapshot.package.root_prefix == "sample"
    assert tuple(snapshot.package.files) == ("LICENSE", "SKILL.md", "references/rules.md")
    assert snapshot.package.license_files == ("LICENSE",)
    assert len(snapshot.package.snapshot_hash) == 64


@pytest.mark.parametrize(
    ("archive", "message"),
    [
        (_zip({"../SKILL.md": "bad"}), "路径穿越"),
        (_zip({"sample/SKILL.md": "ok"}, symlink="sample/link.md"), "软链接"),
        (_zip({"sample/SKILL.md": b"text\x00binary"}), "二进制"),
        (_zip({"sample/SKILL.md": "ok", "sample/tool.py": "print('execute')"}), "只允许"),
        (_zip({"sample/SKILL.md": "ok", "sample/skill.md": "collision"}), "冲突路径"),
        (_zip({"sample/SKILL.md": "A" * (512 * 1024 + 1)}), "512KB"),
    ],
)
def test_package_guard_rejects_unsafe_entries(archive: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SkillPackageGuard().inspect_zip(archive)


def test_package_guard_rejects_compression_bomb_ratio() -> None:
    archive = _zip({"sample/SKILL.md": "A" * 200_000})
    with pytest.raises(ValueError, match="压缩比异常"):
        SkillPackageGuard().inspect_zip(archive)


def test_github_source_requires_full_commit_and_builds_pinned_archive_url() -> None:
    archive = _zip({"repo-deadbeef/skills/sample/SKILL.md": "# Sample"})
    requested_urls: list[str] = []

    def download(url: str) -> bytes:
        requested_urls.append(url)
        return archive

    sha = "A" * 40
    snapshot = SkillSourceFetcher(downloader=download).from_github(
        repo_url="https://github.com/example/expert-skills.git",
        commit_sha=sha,
        path="skills/sample",
    )

    assert snapshot.repo_url == "https://github.com/example/expert-skills"
    assert snapshot.commit_sha == sha.lower()
    assert requested_urls == [
        f"https://codeload.github.com/example/expert-skills/zip/{sha.lower()}"
    ]
    assert snapshot.package.files == {"SKILL.md": "# Sample"}


@pytest.mark.parametrize(
    ("repo_url", "commit"),
    [
        ("http://github.com/example/repo", "a" * 40),
        ("https://gitlab.com/example/repo", "a" * 40),
        ("https://github.com/example/repo/../../evil", "a" * 40),
        ("https://github.com/example/repo", "main"),
        ("https://github.com/example/repo", "a" * 39),
    ],
)
def test_github_source_rejects_untrusted_url_or_movable_revision(repo_url: str, commit: str) -> None:
    with pytest.raises(ValueError):
        SkillSourceFetcher(downloader=lambda _: b"").from_github(
            repo_url=repo_url,
            commit_sha=commit,
        )
