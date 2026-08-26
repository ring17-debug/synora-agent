"""
Synora Context Intelligence V1.

Tujuan:
- menemukan file project yang relevan dengan task
- memberikan relevance score secara deterministic
- mendukung role-aware context
- membatasi ukuran context
- menghindari .git, .venv, target, secret, dan agent state
- tidak membutuhkan API key
- tidak memodifikasi filesystem

Context Engine sengaja deterministic pada V1.
LLM digunakan setelah context selesai dipilih.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


# ============================================================
# CONFIG
# ============================================================

DEFAULT_MAX_FILES = 12
DEFAULT_MAX_CONTEXT_CHARS = 32_000
DEFAULT_MAX_FILE_CHARS = 12_000

IGNORED_DIRS = {
    ".git",
    ".venv",
    "target",
    "__pycache__",
    ".synora-agent",
    "node_modules",
    ".idea",
    ".vscode",
}

PROTECTED_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
}

ALLOWED_EXTENSIONS = {
    ".rs",
    ".toml",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".py",
    ".sh",
    ".txt",
}

ROLE_HINTS = {
    "planner": {
        "cargo.toml",
        "readme",
        "architecture",
        "lib.rs",
        "main.rs",
    },
    "coder": {
        "src",
        ".rs",
        "cargo.toml",
        "lib.rs",
        "main.rs",
    },
    "reviewer": {
        ".rs",
        "cargo.toml",
        "auth",
        "security",
        "rpc",
        "transaction",
    },
    "tester": {
        "test",
        "tests",
        ".rs",
        "cargo.toml",
        "rpc",
    },
    "debugger": {
        ".rs",
        "cargo.toml",
        "error",
        "rpc",
        "transaction",
        "state",
        "mempool",
    },
}


# ============================================================
# DATA TYPES
# ============================================================

@dataclass(frozen=True)
class ContextFile:
    """
    Satu file kandidat context.
    """

    path: str
    score: float
    size: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextResult:
    """
    Hasil final context selection.
    """

    task: str
    role: str | None
    files: tuple[ContextFile, ...]
    context: str
    total_chars: int

    def paths(self) -> list[str]:
        return [
            item.path
            for item in self.files
        ]


# ============================================================
# ENGINE
# ============================================================

class ContextEngine:
    """
    Deterministic project context selector.

    Tidak menggunakan Gemini.

    Pipeline:

        project files
             ↓
        filtering
             ↓
        token/keyword extraction
             ↓
        relevance scoring
             ↓
        role hints
             ↓
        context budget
             ↓
        ContextResult
    """

    def __init__(
        self,
        root: Path | str,
        *,
        max_files: int = DEFAULT_MAX_FILES,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        max_file_chars: int = DEFAULT_MAX_FILE_CHARS,
    ) -> None:

        self.root = Path(root).resolve()

        if not self.root.exists():
            raise ValueError(
                f"Project root tidak ditemukan: {self.root}"
            )

        if not self.root.is_dir():
            raise ValueError(
                f"Project root bukan directory: {self.root}"
            )

        if max_files < 1:
            raise ValueError(
                "max_files harus >= 1."
            )

        if max_context_chars < 1:
            raise ValueError(
                "max_context_chars harus >= 1."
            )

        if max_file_chars < 1:
            raise ValueError(
                "max_file_chars harus >= 1."
            )

        self.max_files = max_files
        self.max_context_chars = max_context_chars
        self.max_file_chars = max_file_chars

    # --------------------------------------------------------
    # PATH
    # --------------------------------------------------------

    def _relative_path(
        self,
        path: Path,
    ) -> str:

        return path.relative_to(
            self.root
        ).as_posix()

    def _is_ignored(
        self,
        path: Path,
    ) -> bool:

        relative = self._relative_path(path)

        parts = Path(relative).parts

        for part in parts:
            if part in IGNORED_DIRS:
                return True

        if path.name in PROTECTED_FILES:
            return True

        if path.name.startswith(".env."):
            return True

        return False

    def _is_allowed_file(
        self,
        path: Path,
    ) -> bool:

        if not path.is_file():
            return False

        if self._is_ignored(path):
            return False

        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            return False

        return True

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    def discover_files(self) -> list[Path]:
        """
        Discover source files yang aman untuk dianalisis.
        """

        result: list[Path] = []

        for path in self.root.rglob("*"):

            if self._is_allowed_file(path):
                result.append(path)

        result.sort(
            key=lambda item: self._relative_path(item)
        )

        return result

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(
            r"[^a-zA-Z0-9_./:-]+",
            " ",
            text.lower(),
        )

    @staticmethod
    def _tokens(text: str) -> set[str]:
        """
        Mengambil token bermakna dari task/path.
        """

        normalized = ContextEngine._normalize(
            text
        )

        raw = normalized.split()

        tokens: set[str] = set()

        for token in raw:
            token = token.strip(
                "./:_-"
            )

            if len(token) < 2:
                continue

            tokens.add(token)

        return tokens

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    def _score_file(
        self,
        path: Path,
        task: str,
        role: str | None,
    ) -> ContextFile:

        relative = self._relative_path(path)

        path_lower = relative.lower()
        task_lower = task.lower()

        task_tokens = self._tokens(task)

        path_tokens = self._tokens(
            relative
        )

        score = 0.0
        reasons: list[str] = []

        # ----------------------------------------------------
        # Exact filename
        # ----------------------------------------------------

        filename = path.name.lower()

        if filename in task_lower:
            score += 12.0
            reasons.append(
                "nama file cocok dengan task"
            )

        # ----------------------------------------------------
        # Path token matches
        # ----------------------------------------------------

        matched_tokens = (
            task_tokens & path_tokens
        )

        if matched_tokens:
            points = min(
                18.0,
                len(matched_tokens) * 4.0,
            )

            score += points

            reasons.append(
                "path memiliki token: "
                + ", ".join(
                    sorted(matched_tokens)
                )
            )

        # ----------------------------------------------------
        # Semantic keyword matches
        # ----------------------------------------------------

        semantic_groups = {
            "rpc": {
                "rpc",
                "endpoint",
                "http",
                "api",
                "jsonrpc",
            },
            "transaction": {
                "transaction",
                "tx",
                "mempool",
            },
            "state": {
                "state",
                "storage",
                "database",
                "db",
            },
            "block": {
                "block",
                "chain",
                "consensus",
            },
            "authentication": {
                "auth",
                "authentication",
                "authorization",
                "login",
                "token",
                "permission",
                "security",
            },
            "testing": {
                "test",
                "testing",
                "coverage",
                "regression",
            },
            "debugging": {
                "error",
                "bug",
                "crash",
                "panic",
                "exception",
                "failure",
            },
        }

        for group, keywords in semantic_groups.items():

            task_has_group = any(
                keyword in task_lower
                for keyword in keywords
            )

            if not task_has_group:
                continue

            file_has_group = (
                group in path_lower
                or any(
                    keyword in path_lower
                    for keyword in keywords
                )
            )

            if file_has_group:
                score += 8.0
                reasons.append(
                    f"relevan dengan domain {group}"
                )

        # ----------------------------------------------------
        # Source priority
        # ----------------------------------------------------

        if filename in {
            "cargo.toml",
            "lib.rs",
            "main.rs",
        }:
            score += 2.0
            reasons.append(
                "source entry point"
            )

        # ----------------------------------------------------
        # Role hints
        # ----------------------------------------------------

        if role:
            hints = ROLE_HINTS.get(
                role,
                set(),
            )

            for hint in hints:

                if hint in path_lower:
                    score += 3.0

                    reasons.append(
                        f"role hint: {hint}"
                    )

        # ----------------------------------------------------
        # File size
        # ----------------------------------------------------

        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        # Small files receive a small preference.
        if 0 < size <= self.max_file_chars:
            score += 1.0

        # Empty files are not useful.
        if size == 0:
            score -= 2.0

        # Extremely large files are still eligible,
        # but are penalized.
        if size > self.max_file_chars:
            score -= 2.0

        return ContextFile(
            path=relative,
            score=round(
                score,
                3,
            ),
            size=size,
            reasons=tuple(
                dict.fromkeys(reasons)
            ),
        )

    # --------------------------------------------------------
    # RANK
    # --------------------------------------------------------

    def rank_files(
        self,
        task: str,
        role: str | None = None,
    ) -> list[ContextFile]:
        """
        Ranking deterministic berdasarkan task + role.
        """

        if not isinstance(task, str):
            raise TypeError(
                "task harus string."
            )

        if not task.strip():
            raise ValueError(
                "task tidak boleh kosong."
            )

        candidates = self.discover_files()

        scored = [
            self._score_file(
                path,
                task,
                role,
            )
            for path in candidates
        ]

        scored.sort(
            key=lambda item: (
                -item.score,
                item.path,
            )
        )

        return scored

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def _read_file(
        self,
        relative_path: str,
    ) -> str:

        path = (
            self.root
            / relative_path
        ).resolve()

        try:
            path.relative_to(
                self.root
            )
        except ValueError as error:
            raise RuntimeError(
                f"Context path keluar project: "
                f"{relative_path}"
            ) from error

        if not self._is_allowed_file(path):
            raise RuntimeError(
                f"Context file tidak diizinkan: "
                f"{relative_path}"
            )

        try:
            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            raise RuntimeError(
                f"Gagal membaca context "
                f"{relative_path}: {error}"
            ) from error

        if len(text) > self.max_file_chars:
            text = text[
                : self.max_file_chars
            ]

            text += (
                "\n\n"
                "[CONTEXT TRUNCATED]\n"
            )

        return text

    # --------------------------------------------------------
    # BUILD
    # --------------------------------------------------------

    def build_context(
        self,
        task: str,
        role: str | None = None,
    ) -> ContextResult:
        """
        Pilih file berdasarkan ranking dan budget.
        """

        ranked = self.rank_files(
            task,
            role,
        )

        selected: list[ContextFile] = []
        chunks: list[str] = []

        total_chars = 0

        for item in ranked:

            if len(selected) >= self.max_files:
                break

            remaining = (
                self.max_context_chars
                - total_chars
            )

            if remaining <= 0:
                break

            text = self._read_file(
                item.path
            )

            if not text.strip():
                continue

            if len(text) > remaining:
                text = text[
                    :remaining
                ]

                text += (
                    "\n\n"
                    "[CONTEXT BUDGET REACHED]\n"
                )

            chunk = (
                "\n"
                + "=" * 60
                + "\n"
                + f"FILE: {item.path}\n"
                + f"SCORE: {item.score:.3f}\n"
                + "=" * 60
                + "\n"
                + text
                + "\n"
            )

            if len(chunk) > remaining:
                break

            chunks.append(chunk)

            total_chars += len(chunk)
            selected.append(item)

        return ContextResult(
            task=task,
            role=role,
            files=tuple(selected),
            context="".join(chunks),
            total_chars=total_chars,
        )

    # --------------------------------------------------------
    # CONVENIENCE
    # --------------------------------------------------------

    def select_files(
        self,
        task: str,
        role: str | None = None,
    ) -> list[str]:
        """
        Hanya mengembalikan path file terpilih.
        """

        result = self.build_context(
            task,
            role,
        )

        return result.paths()


# ============================================================
# FACTORY
# ============================================================

def create_context_engine(
    root: Path | str,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    max_file_chars: int = DEFAULT_MAX_FILE_CHARS,
) -> ContextEngine:

    return ContextEngine(
        root,
        max_files=max_files,
        max_context_chars=max_context_chars,
        max_file_chars=max_file_chars,
    )


__all__ = [
    "ContextFile",
    "ContextResult",
    "ContextEngine",
    "create_context_engine",
]
