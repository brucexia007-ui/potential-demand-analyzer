"""Skill V2 的 Workspace 隔离文件存储。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from uuid import UUID


_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SOURCE_BYTES = 1_048_576
_MAX_IMPORT_ARCHIVE_BYTES = 2 * 1024 * 1024
_REFERENCE_EXTENSIONS = frozenset({".json", ".md", ".txt", ".yaml", ".yml"})
_MAX_REFERENCE_FILES = 64
_MAX_REFERENCE_FILE_BYTES = 256 * 1024
_MAX_REFERENCE_TOTAL_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StoredSkillSource:
    source_ref: str
    content_hash: str
    size_bytes: int


class SkillFileStore:
    """保存工作区草稿、不可变版本快照和已发布运行时目录。"""

    def __init__(
        self,
        *,
        base_dir: Path | str | None = None,
        system_root: Path | str | None = None,
    ) -> None:
        data_root = Path(__file__).resolve().parents[2] / "data"
        configured_root = base_dir or os.getenv("SKILL_WORKSPACE_ROOT") or data_root / "workspace_skills"
        self._base_dir = Path(configured_root).resolve()
        self._system_root = Path(system_root or data_root / "skills").resolve()

    @property
    def system_root(self) -> Path:
        return self._system_root

    def workspace_catalog_root(self, workspace_id: UUID) -> Path:
        return self._resolve(self._workspace_ref(workspace_id, "published"))

    def write_draft(
        self,
        *,
        workspace_id: UUID,
        name: str,
        markdown: str,
        files: dict[str, str] | None = None,
    ) -> StoredSkillSource:
        self._validate_name(name)
        bundle = self._validated_skill_bundle(markdown=markdown, files=files)
        source_ref = self._workspace_ref(workspace_id, "drafts", name, "SKILL.md")
        self._replace_bundle(self._resolve(source_ref).parent, bundle)
        return self._stored_bundle(source_ref, bundle)

    def snapshot_version(
        self,
        *,
        workspace_id: UUID,
        name: str,
        version: int,
        markdown: str,
        files: dict[str, str] | None = None,
    ) -> StoredSkillSource:
        self._validate_name(name)
        if version < 1:
            raise ValueError("Skill 版本必须大于 0")
        bundle = self._validated_skill_bundle(markdown=markdown, files=files)
        source_ref = self._workspace_ref(
            workspace_id, "versions", name, str(version), "SKILL.md"
        )
        destination = self._resolve(source_ref)
        if destination.parent.exists():
            if self._read_skill_bundle(destination.parent) != bundle:
                raise FileExistsError("Skill 版本快照不可修改")
            return self._stored_bundle(source_ref, bundle)
        self._write_new_bundle(destination.parent, bundle)
        return self._stored_bundle(source_ref, bundle)

    def snapshot_system_version(
        self,
        *,
        name: str,
        version: int,
        markdown: str,
        files: dict[str, str] | None = None,
    ) -> StoredSkillSource:
        self._validate_name(name)
        if version < 1:
            raise ValueError("Skill 版本必须大于 0")
        bundle = self._validated_skill_bundle(markdown=markdown, files=files)
        source_ref = PurePosixPath(
            "system_versions", name, str(version), "SKILL.md"
        ).as_posix()
        destination = self._resolve(source_ref)
        if destination.parent.exists():
            if self._read_skill_bundle(destination.parent) != bundle:
                raise FileExistsError("系统 Skill 版本快照不可修改")
            return self._stored_bundle(source_ref, bundle)
        self._write_new_bundle(destination.parent, bundle)
        return self._stored_bundle(source_ref, bundle)

    def publish_version(
        self,
        *,
        workspace_id: UUID,
        name: str,
        source_ref: str,
    ) -> StoredSkillSource:
        self._validate_name(name)
        expected_prefix = self._workspace_ref(workspace_id, "versions", name)
        normalized = self._normalize_ref(source_ref)
        if not normalized.startswith(f"{expected_prefix}/"):
            raise ValueError("只能发布当前 Workspace 中该 Skill 的版本快照")
        source_path = self._resolve(source_ref)
        bundle = self._read_skill_bundle(source_path.parent)
        published_ref = self._workspace_ref(workspace_id, "published", name, "SKILL.md")
        self._replace_bundle(self._resolve(published_ref).parent, bundle)
        return self._stored_bundle(published_ref, bundle)

    def read(self, source_ref: str) -> str:
        path = self._resolve(source_ref)
        if not path.is_file():
            raise FileNotFoundError(source_ref)
        return path.read_text(encoding="utf-8")

    def read_system_bundle(self, *, name: str) -> dict[str, str]:
        """读取并校验内置 Skill 的完整声明式文件包。"""
        self._validate_name(name)
        skill_dir = (self.system_root / name).resolve()
        try:
            skill_dir.relative_to(self.system_root)
        except ValueError as error:
            raise ValueError("系统 Skill 路径越界") from error
        if not skill_dir.is_dir() or skill_dir.is_symlink():
            raise LookupError(f"系统 Skill 不存在: {name}")
        bundle = self._read_skill_bundle(skill_dir)
        return {path: content.decode("utf-8") for path, content in bundle.items()}

    def read_system(self, name: str) -> str:
        self._validate_name(name)
        path = (self._system_root / name / "SKILL.md").resolve()
        try:
            path.relative_to(self._system_root)
        except ValueError as error:
            raise ValueError("系统 Skill 路径越界") from error
        if not path.is_file():
            raise FileNotFoundError(name)
        return path.read_text(encoding="utf-8")

    def snapshot_import_bundle(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        kind: str,
        files: dict[str, str],
    ) -> StoredSkillSource:
        """写入只读导入快照；原始包和转换结果使用不同文件。"""
        if kind not in {"source", "converted", "merged"}:
            raise ValueError("导入快照类型不合法")
        if not files or "SKILL.md" not in files:
            raise ValueError("导入快照必须包含 SKILL.md")
        for path, content in files.items():
            self._normalize_ref(path)
            if not isinstance(content, str) or "\x00" in content:
                raise ValueError("导入快照只允许 UTF-8 文本")
        payload = json.dumps(
            files,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > 6 * 1024 * 1024:
            raise ValueError("导入快照超过 6MB 上限")
        source_ref = self._workspace_ref(
            workspace_id, "imports", str(job_id), f"{kind}.json"
        )
        destination = self._resolve(source_ref)
        if destination.exists():
            if destination.read_bytes() != payload:
                raise FileExistsError("Skill 导入快照不可修改")
            return self._stored(source_ref, payload)
        self._atomic_write(destination, payload)
        return self._stored(source_ref, payload)

    def snapshot_import_archive(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        archive: bytes,
    ) -> StoredSkillSource:
        """保存待 Worker 检查的不可变离线原包，不进行解压或执行。"""
        if not archive:
            raise ValueError("Skill 包为空")
        if len(archive) > _MAX_IMPORT_ARCHIVE_BYTES:
            raise ValueError("Skill 压缩包超过 2MB 上限")
        source_ref = self._workspace_ref(
            workspace_id, "imports", str(job_id), "source.zip"
        )
        destination = self._resolve(source_ref)
        if destination.exists():
            if destination.read_bytes() != archive:
                raise FileExistsError("Skill 导入原包快照不可修改")
            return self._stored(source_ref, archive)
        self._atomic_write(destination, archive)
        return self._stored(source_ref, archive)

    def read_import_archive(self, source_ref: str) -> bytes:
        normalized = self._normalize_ref(source_ref)
        if "/imports/" not in f"/{normalized}/" or not normalized.endswith("/source.zip"):
            raise ValueError("不是受控 Skill 导入原包")
        path = self._resolve(normalized)
        if not path.is_file():
            raise FileNotFoundError(source_ref)
        payload = path.read_bytes()
        if len(payload) > _MAX_IMPORT_ARCHIVE_BYTES:
            raise ValueError("Skill 导入原包超过 2MB 上限")
        return payload

    def read_import_bundle(self, source_ref: str) -> dict[str, str]:
        normalized = self._normalize_ref(source_ref)
        if "/imports/" not in f"/{normalized}/" or not normalized.endswith(".json"):
            raise ValueError("不是受控 Skill 导入快照")
        payload = json.loads(self.read(source_ref))
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        ):
            raise ValueError("Skill 导入快照结构损坏")
        return payload

    def _resolve(self, source_ref: str) -> Path:
        normalized = self._normalize_ref(source_ref)
        destination = (self._base_dir / Path(*PurePosixPath(normalized).parts)).resolve()
        try:
            destination.relative_to(self._base_dir)
        except ValueError as error:
            raise ValueError("Skill 文件路径越界") from error
        current = self._base_dir
        for part in destination.relative_to(self._base_dir).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError("Skill 文件路径不允许符号链接")
        return destination

    @staticmethod
    def _normalize_ref(source_ref: str) -> str:
        relative = PurePosixPath(source_ref)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("Skill 文件路径非法")
        return relative.as_posix()

    @staticmethod
    def _workspace_ref(workspace_id: UUID, *parts: str) -> str:
        return PurePosixPath(f"workspace_{workspace_id}", *parts).as_posix()

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _SKILL_NAME.fullmatch(name):
            raise ValueError("Skill 名称只能使用小写字母、数字和单连字符")

    @staticmethod
    def _validate_markdown(markdown: str) -> bytes:
        if "\x00" in markdown:
            raise ValueError("SKILL.md 不允许包含空字节")
        payload = markdown.encode("utf-8")
        if not payload:
            raise ValueError("SKILL.md 不能为空")
        if len(payload) > _MAX_SOURCE_BYTES:
            raise ValueError("SKILL.md 不能超过 1 MiB")
        return payload

    @classmethod
    def _validated_skill_bundle(
        cls,
        *,
        markdown: str,
        files: dict[str, str] | None,
    ) -> dict[str, bytes]:
        markdown_payload = cls._validate_markdown(markdown)
        raw_files = dict(files or {"SKILL.md": markdown})
        if raw_files.get("SKILL.md") != markdown:
            raise ValueError("Skill 文件包中的 SKILL.md 必须与 markdown 一致")
        if len(raw_files) > _MAX_REFERENCE_FILES + 1:
            raise ValueError("references 文件数超过限制")
        bundle: dict[str, bytes] = {"SKILL.md": markdown_payload}
        reference_total = 0
        for raw_path, content in raw_files.items():
            if raw_path == "SKILL.md":
                continue
            if not isinstance(raw_path, str) or "\\" in raw_path:
                raise ValueError("references 路径非法")
            relative = PurePosixPath(raw_path)
            if (
                relative.is_absolute()
                or not relative.parts
                or relative.parts[0] != "references"
                or ".." in relative.parts
                or "." in relative.parts
            ):
                raise ValueError("references 路径必须位于 references/ 目录")
            if relative.suffix.lower() not in _REFERENCE_EXTENSIONS:
                raise ValueError(f"references 文件类型不受支持：{raw_path}")
            if not isinstance(content, str) or "\x00" in content:
                raise ValueError("references 只允许不含空字节的 UTF-8 文本")
            payload = content.encode("utf-8")
            if len(payload) > _MAX_REFERENCE_FILE_BYTES:
                raise ValueError(f"references 单文件超过限制：{raw_path}")
            reference_total += len(payload)
            if reference_total > _MAX_REFERENCE_TOTAL_BYTES:
                raise ValueError("references 总大小超过限制")
            normalized = relative.as_posix()
            if normalized in bundle:
                raise ValueError(f"references 路径重复：{normalized}")
            bundle[normalized] = payload
        return dict(sorted(bundle.items()))

    @classmethod
    def _read_skill_bundle(cls, root: Path) -> dict[str, bytes]:
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Skill 文件包目录非法")
        skill_path = root / "SKILL.md"
        if not skill_path.is_file() or skill_path.is_symlink():
            raise ValueError("Skill 文件包缺少普通 SKILL.md")
        raw: dict[str, str] = {
            "SKILL.md": skill_path.read_text(encoding="utf-8"),
        }
        reference_root = root / "references"
        if reference_root.exists():
            if reference_root.is_symlink() or not reference_root.is_dir():
                raise ValueError("references 必须是普通目录")
            for path in sorted(reference_root.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_symlink():
                    raise ValueError("references 不允许符号链接")
                if path.is_file():
                    raw[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
        return cls._validated_skill_bundle(markdown=raw["SKILL.md"], files=raw)

    @staticmethod
    def _bundle_hash(bundle: dict[str, bytes]) -> str:
        if set(bundle) == {"SKILL.md"}:
            return sha256(bundle["SKILL.md"]).hexdigest()
        digest = sha256()
        for path, payload in sorted(bundle.items()):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(payload)
            digest.update(b"\0")
        return digest.hexdigest()

    @classmethod
    def _stored_bundle(
        cls,
        source_ref: str,
        bundle: dict[str, bytes],
    ) -> StoredSkillSource:
        return StoredSkillSource(
            source_ref=source_ref,
            content_hash=cls._bundle_hash(bundle),
            size_bytes=sum(len(payload) for payload in bundle.values()),
        )

    @classmethod
    def _write_new_bundle(cls, destination: Path, bundle: dict[str, bytes]) -> None:
        if destination.exists():
            raise FileExistsError("Skill 文件包目录已存在")
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.writing")
        if temporary.exists():
            shutil.rmtree(temporary)
        try:
            for path, payload in bundle.items():
                target = temporary / Path(*PurePosixPath(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    @classmethod
    def _replace_bundle(cls, destination: Path, bundle: dict[str, bytes]) -> None:
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.writing")
        backup = destination.with_name(f".{destination.name}.{os.getpid()}.backup")
        for path in (temporary, backup):
            if path.exists():
                shutil.rmtree(path)
        try:
            for path, payload in bundle.items():
                target = temporary / Path(*PurePosixPath(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                os.replace(destination, backup)
            os.replace(temporary, destination)
            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            if destination.exists():
                shutil.rmtree(destination)
            if backup.exists():
                os.replace(backup, destination)
            raise
        finally:
            for path in (temporary, backup):
                if path.exists():
                    shutil.rmtree(path)

    @staticmethod
    def _stored(source_ref: str, payload: bytes) -> StoredSkillSource:
        return StoredSkillSource(
            source_ref=source_ref,
            content_hash=sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )

    @staticmethod
    def _atomic_write(destination: Path, payload: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.writing")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
