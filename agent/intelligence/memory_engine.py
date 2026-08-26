"""
Synora Memory Intelligence V1.

Tujuan:
- menyimpan fakta project yang berguna
- menyimpan keputusan agent
- menyimpan hasil eksekusi
- menyimpan error/fix yang pernah terjadi
- melakukan pencarian memory secara deterministic
- tidak menyimpan secret
- tidak membutuhkan Gemini/API key
- atomic write untuk mengurangi risiko corrupt
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


# ============================================================
# CONFIG
# ============================================================

DEFAULT_MAX_ENTRIES = 500
DEFAULT_MAX_RESULT = 10
DEFAULT_MAX_TEXT = 20_000

PROTECTED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials",
    "secret",
    "secrets",
    "token",
    "password",
    "apikey",
    "api_key",
}

SENSITIVE_KEYWORDS = {
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "private_key",
    "mnemonic",
    "seed_phrase",
    "credential",
    "credentials",
}


# ============================================================
# DATA TYPES
# ============================================================

@dataclass(frozen=True)
class MemoryEntry:
    """
    Satu memory item.
    """

    id: str
    kind: str
    title: str
    content: str
    tags: tuple[str, ...] = ()
    created_at: str = ""
    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class MemorySearchResult:
    """
    Memory hasil pencarian dengan score.
    """

    entry: MemoryEntry
    score: float
    matches: tuple[str, ...] = ()


# ============================================================
# ENGINE
# ============================================================

class MemoryEngine:
    """
    Persistent local memory engine.

    Storage:

        .synora-agent/memory.json

    Memory tidak boleh digunakan untuk menyimpan secret.
    """

    def __init__(
        self,
        memory_file: Path | str,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_text: int = DEFAULT_MAX_TEXT,
    ) -> None:

        self.memory_file = Path(
            memory_file
        ).resolve()

        if max_entries < 1:
            raise ValueError(
                "max_entries harus >= 1."
            )

        if max_text < 100:
            raise ValueError(
                "max_text terlalu kecil."
            )

        self.max_entries = max_entries
        self.max_text = max_text

    # ========================================================
    # TIME
    # ========================================================

    @staticmethod
    def _now() -> str:
        return datetime.now(
            timezone.utc
        ).isoformat(
            timespec="seconds"
        )

    # ========================================================
    # NORMALIZATION
    # ========================================================

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(
            r"[^a-zA-Z0-9_./:-]+",
            " ",
            text.lower(),
        )

    @staticmethod
    def _tokens(text: str) -> set[str]:
        normalized = MemoryEngine._normalize(
            text
        )

        return {
            token.strip(
                "./:_-"
            )
            for token in normalized.split()
            if len(
                token.strip("./:_-")
            ) >= 2
        }

    # ========================================================
    # SECRET SAFETY
    # ========================================================

    @staticmethod
    def _contains_sensitive_key(
        text: str,
    ) -> bool:

        normalized = text.lower()

        for keyword in SENSITIVE_KEYWORDS:
            pattern = (
                r"\b"
                + re.escape(keyword)
                + r"\b"
            )

            if re.search(
                pattern,
                normalized,
            ):
                return True

        return False

    @staticmethod
    def _looks_like_secret(
        text: str,
    ) -> bool:

        patterns = (
            r"AIza[0-9A-Za-z_-]{20,}",
            r"sk-[A-Za-z0-9_-]{20,}",
            r"ghp_[A-Za-z0-9]{20,}",
            r"-----BEGIN .* PRIVATE KEY-----",
            r"Bearer\s+[A-Za-z0-9._-]{20,}",
        )

        return any(
            re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )
            for pattern in patterns
        )

    @classmethod
    def _validate_safe_text(
        cls,
        text: str,
        field_name: str,
    ) -> None:

        if not isinstance(
            text,
            str,
        ):
            raise TypeError(
                f"{field_name} harus string."
            )

        if not text.strip():
            raise ValueError(
                f"{field_name} tidak boleh kosong."
            )

        if len(text) > DEFAULT_MAX_TEXT:
            raise ValueError(
                f"{field_name} terlalu panjang."
            )

        if cls._looks_like_secret(text):
            raise ValueError(
                f"{field_name} terlihat mengandung secret."
            )

    @classmethod
    def _sanitize_metadata(
        cls,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:

        if metadata is None:
            return {}

        if not isinstance(
            metadata,
            dict,
        ):
            raise TypeError(
                "metadata harus dictionary."
            )

        safe: dict[str, Any] = {}

        for key, value in metadata.items():

            key_text = str(key).lower()

            if any(
                sensitive in key_text
                for sensitive in SENSITIVE_KEYWORDS
            ):
                continue

            if isinstance(
                value,
                str,
            ):
                if cls._looks_like_secret(
                    value
                ):
                    continue

                if len(value) > 2_000:
                    value = value[:2_000]

            safe[str(key)] = value

        return safe

    # ========================================================
    # STORAGE
    # ========================================================

    def _ensure_parent(self) -> None:
        self.memory_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _read_raw(self) -> list[dict[str, Any]]:
        """
        Membaca memory dari disk.

        Mendukung dua format:

        1. Format V1:
           [
               {
                   "id": "...",
                   "kind": "...",
                   "title": "...",
                   "content": "..."
               }
           ]

        2. Format legacy:
           {
               "project": "...",
               "facts": [],
               "decisions": [],
               "tasks": [],
               ...
           }

        Format legacy dikonversi secara in-memory
        menjadi format MemoryEntry V1.
        """

        if not self.memory_file.exists():
            return []

        try:
            text = self.memory_file.read_text(
                encoding="utf-8"
            )
        except OSError as error:
            raise RuntimeError(
                f"Gagal membaca memory: {error}"
            ) from error

        if not text.strip():
            return []

        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"memory.json corrupt: {error}"
            ) from error

        # ====================================================
        # FORMAT V1
        # ====================================================

        if isinstance(data, list):
            return [
                item
                for item in data
                if isinstance(item, dict)
            ]

        # ====================================================
        # LEGACY FORMAT
        # ====================================================

        if not isinstance(data, dict):
            raise RuntimeError(
                "Format memory harus JSON list atau legacy object."
            )

        converted: list[dict[str, Any]] = []

        # ====================================================
        # HELPER
        # ====================================================

        def add_legacy_entry(
            *,
            kind: str,
            title: str,
            content: str,
            tags: list[str] | tuple[str, ...] = (),
            metadata: dict[str, Any] | None = None,
            created_at: str = "",
        ) -> None:

            if not isinstance(content, str):
                content = str(content)

            if not content.strip():
                return

            timestamp = (
                created_at
                or str(
                    data.get(
                        "created_at",
                        "",
                    )
                )
                or self._now()
            )

            entry_id = self._make_id(
                kind,
                title,
                timestamp,
            )

            converted.append(
                {
                    "id": entry_id,
                    "kind": kind,
                    "title": title,
                    "content": content,
                    "tags": list(tags),
                    "created_at": timestamp,
                    "metadata": (
                        metadata
                        if isinstance(
                            metadata,
                            dict,
                        )
                        else {}
                    ),
                }
            )

        # ====================================================
        # PROJECT
        # ====================================================

        project = data.get("project")

        if isinstance(project, str) and project.strip():

            add_legacy_entry(
                kind="project_fact",
                title="Project",
                content=(
                    f"Project: {project.strip()}"
                ),
                tags=("project",),
            )

        # ====================================================
        # FACTS
        # ====================================================

        facts = data.get(
            "facts",
            [],
        )

        if isinstance(facts, list):

            for index, item in enumerate(facts):

                if isinstance(item, str):

                    add_legacy_entry(
                        kind="project_fact",
                        title=(
                            f"Legacy fact {index + 1}"
                        ),
                        content=item,
                        tags=(
                            "legacy",
                            "fact",
                        ),
                    )

                elif isinstance(item, dict):

                    title = str(
                        item.get(
                            "title",
                            item.get(
                                "name",
                                f"Legacy fact {index + 1}",
                            ),
                        )
                    )

                    content = str(
                        item.get(
                            "content",
                            item.get(
                                "value",
                                item.get(
                                    "fact",
                                    "",
                                ),
                            ),
                        )
                    )

                    add_legacy_entry(
                        kind="project_fact",
                        title=title,
                        content=content,
                        tags=(
                            "legacy",
                            "fact",
                        ),
                        metadata=item,
                    )

        # ====================================================
        # DECISIONS
        # ====================================================

        decisions = data.get(
            "decisions",
            [],
        )

        if isinstance(decisions, list):

            for index, item in enumerate(decisions):

                if isinstance(item, str):

                    add_legacy_entry(
                        kind="decision",
                        title=(
                            f"Legacy decision {index + 1}"
                        ),
                        content=item,
                        tags=(
                            "legacy",
                            "decision",
                        ),
                    )

                elif isinstance(item, dict):

                    title = str(
                        item.get(
                            "title",
                            item.get(
                                "name",
                                f"Legacy decision {index + 1}",
                            ),
                        )
                    )

                    content = str(
                        item.get(
                            "content",
                            item.get(
                                "decision",
                                item.get(
                                    "value",
                                    "",
                                ),
                            ),
                        )
                    )

                    add_legacy_entry(
                        kind="decision",
                        title=title,
                        content=content,
                        tags=(
                            "legacy",
                            "decision",
                        ),
                        metadata=item,
                    )

        # ====================================================
        # ARCHITECTURE
        # ====================================================

        architecture = data.get(
            "architecture",
            [],
        )

        if isinstance(architecture, list):

            for index, item in enumerate(architecture):

                if isinstance(item, str):

                    add_legacy_entry(
                        kind="project_fact",
                        title=(
                            f"Architecture {index + 1}"
                        ),
                        content=item,
                        tags=(
                            "legacy",
                            "architecture",
                        ),
                    )

                elif isinstance(item, dict):

                    content = json.dumps(
                        item,
                        ensure_ascii=False,
                        indent=2,
                    )

                    add_legacy_entry(
                        kind="project_fact",
                        title=(
                            f"Architecture {index + 1}"
                        ),
                        content=content,
                        tags=(
                            "legacy",
                            "architecture",
                        ),
                        metadata=item,
                    )

        # ====================================================
        # TASK COLLECTIONS
        # ====================================================

        task_fields = (
            "tasks",
            "recent_tasks",
            "completed_tasks",
        )

        for field_name in task_fields:

            items = data.get(
                field_name,
                [],
            )

            if not isinstance(items, list):
                continue

            for index, item in enumerate(items):

                if isinstance(item, str):

                    add_legacy_entry(
                        kind=(
                            "completed_task"
                            if field_name == "completed_tasks"
                            else "task"
                        ),
                        title=(
                            f"{field_name} {index + 1}"
                        ),
                        content=item,
                        tags=(
                            "legacy",
                            "task",
                        ),
                    )

                    continue

                if not isinstance(item, dict):
                    continue

                task = item.get(
                    "task",
                    item.get(
                        "title",
                        item.get(
                            "name",
                            "",
                        ),
                    ),
                )

                files = item.get(
                    "files",
                    [],
                )

                summary = item.get(
                    "summary",
                    "",
                )

                parts: list[str] = []

                if task:
                    parts.append(
                        f"Task: {task}"
                    )

                if summary:
                    parts.append(
                        f"Summary: {summary}"
                    )

                if isinstance(files, list) and files:

                    parts.append(
                        "Files:\\n"
                        + "\\n".join(
                            f"- {file}"
                            for file in files
                        )
                    )

                if not parts:

                    parts.append(
                        json.dumps(
                            item,
                            ensure_ascii=False,
                            indent=2,
                        )
                    )

                add_legacy_entry(
                    kind=(
                        "completed_task"
                        if field_name == "completed_tasks"
                        else "task"
                    ),
                    title=(
                        str(task)
                        if task
                        else f"{field_name} {index + 1}"
                    ),
                    content="\\n".join(parts),
                    tags=(
                        "legacy",
                        "task",
                    ),
                    metadata=item,
                )

        # ====================================================
        # IMPORTANT FILES
        # ====================================================

        important_files = data.get(
            "important_files",
            [],
        )

        if isinstance(
            important_files,
            list,
        ) and important_files:

            content = "\\n".join(
                f"- {file}"
                for file in important_files
            )

            add_legacy_entry(
                kind="project_fact",
                title="Important project files",
                content=content,
                tags=(
                    "legacy",
                    "files",
                ),
                metadata={
                    "files": important_files,
                },
            )

        # ====================================================
        # LAST FILES
        # ====================================================

        last_files = data.get(
            "last_files",
            [],
        )

        if isinstance(
            last_files,
            list,
        ) and last_files:

            content = "\\n".join(
                f"- {file}"
                for file in last_files
            )

            add_legacy_entry(
                kind="project_fact",
                title="Recently used project files",
                content=content,
                tags=(
                    "legacy",
                    "files",
                    "recent",
                ),
                metadata={
                    "files": last_files,
                },
            )

        # ====================================================
        # LEGACY VERSION
        # ====================================================

        legacy_version = data.get(
            "version"
        )

        if legacy_version is not None:

            add_legacy_entry(
                kind="project_fact",
                title="Legacy memory format",
                content=(
                    "Legacy memory format version: "
                    f"{legacy_version}"
                ),
                tags=(
                    "legacy",
                    "memory",
                ),
            )

        return converted

    def _write_raw(
        self,
        data: list[dict[str, Any]],
    ) -> None:

        self._ensure_parent()

        payload = json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )

        # Atomic replacement.
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.memory_file.parent,
            prefix=".memory-",
            suffix=".tmp",
            delete=False,
        ) as temporary:

            temporary.write(
                payload
            )

            temporary_path = Path(
                temporary.name
            )

        try:
            temporary_path.replace(
                self.memory_file
            )
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    # ========================================================
    # CONVERSION
    # ========================================================

    @staticmethod
    def _from_dict(
        data: dict[str, Any],
    ) -> MemoryEntry:

        tags = data.get(
            "tags",
            [],
        )

        if not isinstance(
            tags,
            list,
        ):
            tags = []

        return MemoryEntry(
            id=str(
                data.get(
                    "id",
                    "",
                )
            ),
            kind=str(
                data.get(
                    "kind",
                    "unknown",
                )
            ),
            title=str(
                data.get(
                    "title",
                    "",
                )
            ),
            content=str(
                data.get(
                    "content",
                    "",
                )
            ),
            tags=tuple(
                str(tag)
                for tag in tags
            ),
            created_at=str(
                data.get(
                    "created_at",
                    "",
                )
            ),
            metadata=(
                data.get(
                    "metadata",
                    {},
                )
                if isinstance(
                    data.get(
                        "metadata",
                        {},
                    ),
                    dict,
                )
                else {}
            ),
        )

    # ========================================================
    # ID
    # ========================================================

    @staticmethod
    def _make_id(
        kind: str,
        title: str,
        created_at: str,
    ) -> str:

        base = (
            f"{kind}:"
            f"{title}:"
            f"{created_at}"
        )

        import hashlib

        return hashlib.sha256(
            base.encode(
                "utf-8"
            )
        ).hexdigest()[:16]

    # ========================================================
    # ADD
    # ========================================================

    def add(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        tags: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:

        self._validate_safe_text(
            kind,
            "kind",
        )

        self._validate_safe_text(
            title,
            "title",
        )

        self._validate_safe_text(
            content,
            "content",
        )

        if self._contains_sensitive_key(
            content
        ):
            raise ValueError(
                "Memory content menolak keyword sensitif."
            )

        clean_tags = tuple(
            sorted(
                {
                    str(tag).strip().lower()
                    for tag in (
                        tags or []
                    )
                    if str(tag).strip()
                }
            )
        )

        for tag in clean_tags:
            if self._contains_sensitive_key(
                tag
            ):
                raise ValueError(
                    "Tag memory mengandung keyword sensitif."
                )

        safe_metadata = (
            self._sanitize_metadata(
                metadata
            )
        )

        created_at = self._now()

        entry = MemoryEntry(
            id=self._make_id(
                kind,
                title,
                created_at,
            ),
            kind=kind.strip(),
            title=title.strip(),
            content=content.strip(),
            tags=clean_tags,
            created_at=created_at,
            metadata=safe_metadata,
        )

        data = self._read_raw()

        data.append(
            asdict(entry)
        )

        # Newest memory tetap dipertahankan.
        if len(data) > self.max_entries:
            data = data[
                -self.max_entries:
            ]

        self._write_raw(
            data
        )

        return entry

    # ========================================================
    # LIST
    # ========================================================

    def list(
        self,
        *,
        kind: str | None = None,
        limit: int = DEFAULT_MAX_RESULT,
    ) -> list[MemoryEntry]:

        if limit < 1:
            raise ValueError(
                "limit harus >= 1."
            )

        data = self._read_raw()

        entries = [
            self._from_dict(item)
            for item in data
            if isinstance(
                item,
                dict,
            )
        ]

        if kind is not None:
            entries = [
                entry
                for entry in entries
                if entry.kind == kind
            ]

        entries.reverse()

        return entries[:limit]

    # ========================================================
    # SEARCH
    # ========================================================

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        limit: int = DEFAULT_MAX_RESULT,
    ) -> list[MemorySearchResult]:

        self._validate_safe_text(
            query,
            "query",
        )

        if limit < 1:
            raise ValueError(
                "limit harus >= 1."
            )

        query_tokens = self._tokens(
            query
        )

        if not query_tokens:
            return []

        entries = [
            self._from_dict(item)
            for item in self._read_raw()
            if isinstance(
                item,
                dict,
            )
        ]

        results: list[
            MemorySearchResult
        ] = []

        for entry in entries:

            if (
                kind is not None
                and entry.kind != kind
            ):
                continue

            title_tokens = self._tokens(
                entry.title
            )

            content_tokens = self._tokens(
                entry.content
            )

            tag_tokens = self._tokens(
                " ".join(entry.tags)
            )

            score = 0.0
            matches: list[str] = []

            title_matches = (
                query_tokens
                & title_tokens
            )

            content_matches = (
                query_tokens
                & content_tokens
            )

            tag_matches = (
                query_tokens
                & tag_tokens
            )

            if title_matches:
                score += (
                    len(title_matches)
                    * 8.0
                )

                matches.extend(
                    f"title:{item}"
                    for item in sorted(
                        title_matches
                    )
                )

            if tag_matches:
                score += (
                    len(tag_matches)
                    * 6.0
                )

                matches.extend(
                    f"tag:{item}"
                    for item in sorted(
                        tag_matches
                    )
                )

            if content_matches:
                score += (
                    len(content_matches)
                    * 2.0
                )

                matches.extend(
                    f"content:{item}"
                    for item in sorted(
                        content_matches
                    )
                )

            if score <= 0:
                continue

            # Bonus jika banyak query token ditemukan.
            coverage = (
                len(
                    title_matches
                    | content_matches
                    | tag_matches
                )
                / len(query_tokens)
            )

            score += coverage * 5.0

            results.append(
                MemorySearchResult(
                    entry=entry,
                    score=round(
                        score,
                        3,
                    ),
                    matches=tuple(matches),
                )
            )

        results.sort(
            key=lambda item: (
                -item.score,
                item.entry.created_at,
                item.entry.id,
            ),
            reverse=False,
        )

        return results[:limit]

    # ========================================================
    # CONTEXT
    # ========================================================

    def build_context(
        self,
        query: str,
        *,
        kind: str | None = None,
        limit: int = DEFAULT_MAX_RESULT,
        max_chars: int = 12_000,
    ) -> str:

        if max_chars < 1:
            raise ValueError(
                "max_chars harus >= 1."
            )

        results = self.search(
            query,
            kind=kind,
            limit=limit,
        )

        if not results:
            return ""

        chunks: list[str] = []
        total = 0

        for result in results:

            entry = result.entry

            chunk = (
                "\n"
                + "=" * 60
                + "\n"
                + f"MEMORY: {entry.title}\n"
                + f"KIND: {entry.kind}\n"
                + f"SCORE: {result.score:.3f}\n"
                + f"TAGS: {', '.join(entry.tags)}\n"
                + "=" * 60
                + "\n"
                + entry.content
                + "\n"
            )

            remaining = (
                max_chars - total
            )

            if remaining <= 0:
                break

            if len(chunk) > remaining:
                chunk = chunk[
                    :remaining
                ]

            chunks.append(
                chunk
            )

            total += len(chunk)

            if total >= max_chars:
                break

        return "".join(
            chunks
        )

    # ========================================================
    # REMOVE
    # ========================================================

    def remove(
        self,
        memory_id: str,
    ) -> bool:

        if not isinstance(
            memory_id,
            str,
        ):
            raise TypeError(
                "memory_id harus string."
            )

        data = self._read_raw()

        new_data = [
            item
            for item in data
            if not (
                isinstance(
                    item,
                    dict,
                )
                and str(
                    item.get(
                        "id",
                        "",
                    )
                ) == memory_id
            )
        ]

        if len(new_data) == len(data):
            return False

        self._write_raw(
            new_data
        )

        return True

    # ========================================================
    # CLEAR
    # ========================================================

    def clear(
        self,
        *,
        kind: str | None = None,
    ) -> int:

        data = self._read_raw()

        if kind is None:
            removed = len(data)
            self._write_raw([])
            return removed

        new_data = [
            item
            for item in data
            if not (
                isinstance(
                    item,
                    dict,
                )
                and item.get(
                    "kind"
                ) == kind
            )
        ]

        removed = (
            len(data)
            - len(new_data)
        )

        self._write_raw(
            new_data
        )

        return removed

    # ========================================================
    # STATUS
    # ========================================================

    def status(self) -> dict[str, Any]:

        data = self._read_raw()

        kinds: dict[str, int] = {}

        for item in data:

            if not isinstance(
                item,
                dict,
            ):
                continue

            kind = str(
                item.get(
                    "kind",
                    "unknown",
                )
            )

            kinds[kind] = (
                kinds.get(
                    kind,
                    0,
                )
                + 1
            )

        return {
            "memory_file": str(
                self.memory_file
            ),
            "exists": self.memory_file.exists(),
            "entries": len(data),
            "max_entries": self.max_entries,
            "kinds": kinds,
        }


# ============================================================
# FACTORY
# ============================================================

def create_memory_engine(
    root: Path | str,
) -> MemoryEngine:

    root_path = Path(
        root
    ).resolve()

    memory_file = (
        root_path
        / ".synora-agent"
        / "memory.json"
    )

    return MemoryEngine(
        memory_file
    )


__all__ = [
    "MemoryEntry",
    "MemorySearchResult",
    "MemoryEngine",
    "create_memory_engine",
]
