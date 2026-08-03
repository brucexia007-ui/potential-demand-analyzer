"""固定来源的外部 Skill 获取器；GitHub 只接受完整 Commit SHA。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import httpx

from app.security.outbound_request_guard import OutboundRequestGuard
from app.security.skill_package_guard import GuardedSkillPackage, MAX_ARCHIVE_BYTES, SkillPackageGuard


GITHUB_REPOSITORY_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class SkillSourceSnapshot:
    source_type: str
    repo_url: str | None
    commit_sha: str | None
    requested_path: str
    archive_url: str | None
    package: GuardedSkillPackage


class SkillSourceFetcher:
    def __init__(
        self,
        *,
        downloader: Callable[[str], bytes] | None = None,
        guard: SkillPackageGuard | None = None,
    ):
        self._downloader = downloader or self._download
        self._guard = guard or SkillPackageGuard()

    def from_offline_zip(self, archive: bytes, *, path: str = "") -> SkillSourceSnapshot:
        return SkillSourceSnapshot(
            source_type="OFFLINE_ARCHIVE",
            repo_url=None,
            commit_sha=None,
            requested_path=path,
            archive_url=None,
            package=self._guard.inspect_zip(archive, requested_path=path),
        )

    def from_github(
        self,
        *,
        repo_url: str,
        commit_sha: str,
        path: str = "",
    ) -> SkillSourceSnapshot:
        match = GITHUB_REPOSITORY_PATTERN.fullmatch(repo_url.strip())
        if match is None:
            raise ValueError("只允许 https://github.com/{owner}/{repo} 仓库地址")
        if not COMMIT_SHA_PATTERN.fullmatch(commit_sha):
            raise ValueError("GitHub Skill 必须固定 40 位 Commit SHA，禁止分支或标签")
        owner = match.group("owner")
        repo = match.group("repo")
        canonical_repo = f"https://github.com/{owner}/{repo}"
        normalized_sha = commit_sha.lower()
        archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/{normalized_sha}"
        archive = self._downloader(archive_url)
        return SkillSourceSnapshot(
            source_type="GITHUB",
            repo_url=canonical_repo,
            commit_sha=normalized_sha,
            requested_path=path,
            archive_url=archive_url,
            package=self._guard.inspect_zip(archive, requested_path=path),
        )

    @staticmethod
    def _download(url: str) -> bytes:
        parsed_host = httpx.URL(url).host
        if parsed_host != "codeload.github.com":
            raise ValueError("GitHub 下载目标不合法")
        OutboundRequestGuard.validate_target(url)
        OutboundRequestGuard.resolve_all_and_validate(parsed_host, 443)
        chunks: list[bytes] = []
        total = 0
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            with client.stream("GET", url, headers={"Accept": "application/zip"}) as response:
                if 300 <= response.status_code < 400:
                    raise ValueError("GitHub 固定快照下载禁止重定向")
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError("GitHub Skill 压缩包超过 2MB 上限")
                    chunks.append(chunk)
        return b"".join(chunks)
