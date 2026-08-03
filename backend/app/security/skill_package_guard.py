"""外部 Skill 包静态检查；只读取文本快照，绝不解压执行。"""
from __future__ import annotations

import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath


MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_FILE_COUNT = 100
MAX_COMPRESSION_RATIO = 100
ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".yaml", ".yml", ".json"})
LICENSE_FILENAMES = frozenset({"license", "license.md", "license.txt", "copying", "notice"})
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)


@dataclass(frozen=True)
class GuardedSkillPackage:
    files: dict[str, str]
    snapshot_hash: str
    total_bytes: int
    file_count: int
    root_prefix: str
    license_files: tuple[str, ...]


class SkillPackageGuard:
    def inspect_zip(self, archive: bytes, *, requested_path: str = "") -> GuardedSkillPackage:
        if not archive:
            raise ValueError("Skill 包为空")
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ValueError("Skill 压缩包超过 2MB 上限")
        try:
            package = zipfile.ZipFile(BytesIO(archive))
        except (zipfile.BadZipFile, OSError) as error:
            raise ValueError("Skill 包不是有效 ZIP") from error

        with package:
            infos = [item for item in package.infolist() if not item.is_dir()]
            if len(infos) > MAX_FILE_COUNT:
                raise ValueError("Skill 包文件数超过 100 个")
            if not infos:
                raise ValueError("Skill 包没有文件")
            normalized_entries: list[tuple[zipfile.ZipInfo, str]] = []
            seen_paths: set[str] = set()
            total_bytes = 0
            for info in infos:
                path = self._safe_path(info.filename)
                path_key = unicodedata.normalize("NFC", path).casefold()
                if path_key in seen_paths:
                    raise ValueError(f"Skill 包存在大小写或 Unicode 冲突路径: {path}")
                seen_paths.add(path_key)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError(f"Skill 包禁止软链接: {path}")
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                    raise ValueError(f"Skill 包包含非普通文件: {path}")
                if info.flag_bits & 0x1:
                    raise ValueError(f"Skill 包禁止加密文件: {path}")
                suffix = PurePosixPath(path).suffix.lower()
                if suffix not in ALLOWED_SUFFIXES and PurePosixPath(path).name.lower() not in LICENSE_FILENAMES:
                    raise ValueError(f"Skill 包只允许 Markdown/YAML/JSON/TXT 文本: {path}")
                if info.file_size > MAX_FILE_BYTES:
                    raise ValueError(f"Skill 文件超过 512KB: {path}")
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ValueError(f"Skill 文件压缩比异常: {path}")
                total_bytes += info.file_size
                if total_bytes > MAX_TOTAL_BYTES:
                    raise ValueError("Skill 包解压后超过 5MB 上限")
                normalized_entries.append((info, path))

            prefix = self._select_prefix([path for _, path in normalized_entries], requested_path)
            selected: list[tuple[zipfile.ZipInfo, str]] = []
            for info, path in normalized_entries:
                if prefix and not path.startswith(f"{prefix}/"):
                    continue
                relative_path = path[len(prefix) + 1 :] if prefix else path
                if relative_path:
                    selected.append((info, relative_path))
            if not selected or not any(path == "SKILL.md" for _, path in selected):
                raise ValueError("选定目录缺少根 SKILL.md")

            files: dict[str, str] = {}
            digest = sha256()
            for info, relative_path in sorted(selected, key=lambda item: item[1]):
                raw = package.read(info)
                if b"\x00" in raw:
                    raise ValueError(f"Skill 包包含二进制内容: {relative_path}")
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError as error:
                    raise ValueError(f"Skill 文件不是 UTF-8 文本: {relative_path}") from error
                files[relative_path] = text
                digest.update(relative_path.encode("utf-8"))
                digest.update(b"\x00")
                digest.update(raw)
                digest.update(b"\x00")
            licenses = tuple(path for path in files if PurePosixPath(path).name.lower() in LICENSE_FILENAMES)
            return GuardedSkillPackage(
                files=files,
                snapshot_hash=digest.hexdigest(),
                total_bytes=sum(len(value.encode("utf-8")) for value in files.values()),
                file_count=len(files),
                root_prefix=prefix,
                license_files=licenses,
            )

    @staticmethod
    def _safe_path(value: str) -> str:
        if not value or "\x00" in value or "\\" in value:
            raise ValueError("Skill 包路径不合法")
        normalized = unicodedata.normalize("NFC", value)
        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"Skill 包路径穿越: {value}")
        if len(normalized) > 512:
            raise ValueError("Skill 包路径过长")
        for part in path.parts:
            if part.endswith((" ", ".")):
                raise ValueError(f"Skill 包路径含尾随空格或点: {value}")
            stem = part.split(".", 1)[0].casefold()
            if stem in WINDOWS_RESERVED_NAMES:
                raise ValueError(f"Skill 包路径使用系统保留名称: {value}")
        return path.as_posix()

    @staticmethod
    def _select_prefix(paths: list[str], requested_path: str) -> str:
        requested = requested_path.strip("/")
        if requested:
            safe_requested = SkillPackageGuard._safe_path(requested)
            roots = sorted({path.split("/", 1)[0] for path in paths})
            candidates = [safe_requested, *(f"{root}/{safe_requested}" for root in roots)]
            matches = [candidate for candidate in candidates if f"{candidate}/SKILL.md" in paths]
            if len(matches) != 1:
                raise ValueError("指定 Skill 目录不存在或不唯一")
            return matches[0]
        if "SKILL.md" in paths:
            return ""
        top_levels = {path.split("/", 1)[0] for path in paths if "/" in path}
        matches = [root for root in top_levels if f"{root}/SKILL.md" in paths]
        if len(matches) != 1:
            raise ValueError("Skill 包必须有唯一根目录和 SKILL.md")
        return matches[0]
