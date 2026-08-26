#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai


# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / ".synora-agent"

INDEX_FILE = AGENT_DIR / "project_index.json"
MEMORY_FILE = AGENT_DIR / "memory.json"
HISTORY_FILE = AGENT_DIR / "history.jsonl"

load_dotenv(ROOT / ".env")

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY belum diset di .env")

client = genai.Client(api_key=API_KEY)


# ============================================================
# LIMITS
# ============================================================

MAX_FILE_SIZE = 120_000
MAX_CONTEXT_CHARS = 32_000
MAX_MEMORY_CHARS = 20_000

IGNORED_DIRS = {
    ".git",
    ".venv",
    "target",
    "__pycache__",
    ".synora-agent",
    "node_modules",
}

ALLOWED_EXTENSIONS = {
    ".rs",
    ".toml",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".sh",
    ".py",
}

SAFE_COMMANDS = {
    "cargo fmt --all",
    "cargo check",
    "cargo test",
}


# ============================================================
# UTILITIES
# ============================================================

def ensure_agent_dir():
    AGENT_DIR.mkdir(parents=True, exist_ok=True)


def now():
    return datetime.now().isoformat(timespec="seconds")


def print_header(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def load_json(path, default):
    try:
        if not path.exists():
            return default

        return json.loads(path.read_text(encoding="utf-8"))

    except Exception:
        return default


def save_json(path, data):
    ensure_agent_dir()

    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def append_history(event, data=None):
    ensure_agent_dir()

    record = {
        "timestamp": now(),
        "event": event,
        "data": data or {},
    }

    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ============================================================
# MEMORY
# ============================================================

def load_memory():
    default = {
        "project": {
            "name": "Synora",
            "language": "Rust",
            "description": "Blockchain node and core implementation",
        },
        "facts": [],
        "decisions": [],
        "important_files": [],
        "known_issues": [],
        "completed_tasks": [],
    }

    memory = load_json(MEMORY_FILE, default)

    if not isinstance(memory, dict):
        memory = default

    return memory


def save_memory(memory):
    save_json(MEMORY_FILE, memory)


def memory_context():
    memory = load_memory()

    text = json.dumps(
        memory,
        indent=2,
        ensure_ascii=False,
    )

    return text[:MAX_MEMORY_CHARS]


def update_memory(task, result_summary):
    memory = load_memory()

    completed = memory.setdefault("completed_tasks", [])

    completed.append({
        "timestamp": now(),
        "task": task[:500],
        "summary": result_summary[:1000],
    })

    # Keep memory small.
    if len(completed) > 30:
        del completed[:-30]

    save_memory(memory)


# ============================================================
# PROJECT INDEX
# ============================================================

def should_include(path):
    if not path.is_file():
        return False

    if any(part in IGNORED_DIRS for part in path.parts):
        return False

    # Backup project bukan source aktif.
    # Contoh: .backup-final-..., .backup-v3-..., dll.
    if any(part.startswith(".backup-") for part in path.parts):
        return False

    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return False

    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return False
    except OSError:
        return False

    return True


def build_index():
    ensure_agent_dir()

    print("Membangun index proyek...")

    files = []

    for path in ROOT.rglob("*"):
        if not should_include(path):
            continue

        relative = path.relative_to(ROOT)

        try:
            size = path.stat().st_size
        except OSError:
            continue

        files.append({
            "path": str(relative),
            "size": size,
            "extension": path.suffix.lower(),
        })

    files.sort(key=lambda item: item["path"])

    index = {
        "project": "Synora",
        "root": str(ROOT),
        "generated_at": now(),
        "file_count": len(files),
        "files": files,
    }

    save_json(INDEX_FILE, index)

    print(f"Index selesai: {len(files)} file")
    print(f"Index: {INDEX_FILE}")

    append_history(
        "index_built",
        {"file_count": len(files)},
    )


def load_index():
    if not INDEX_FILE.exists():
        build_index()

    return load_json(
        INDEX_FILE,
        {
            "project": "Synora",
            "files": [],
        },
    )


# ============================================================
# FILE READING
# ============================================================

def read_file(relative_path):
    path = ROOT / relative_path

    try:
        resolved = path.resolve()
        root_resolved = ROOT.resolve()

        if root_resolved not in resolved.parents and resolved != root_resolved:
            return None

        if not path.exists() or not path.is_file():
            return None

        if path.stat().st_size > MAX_FILE_SIZE:
            return (
                f"[FILE TOO LARGE]\n"
                f"{relative_path}\n"
                f"Size: {path.stat().st_size} bytes"
            )

        return path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    except Exception as error:
        return f"[READ ERROR] {relative_path}: {error}"


def search_code(query, limit=20):
    """
    Lightweight local code search.
    Does not depend on ripgrep being installed.
    """

    results = []

    query_lower = query.lower()

    index = load_index()

    for item in index.get("files", []):
        path = item["path"]

        content = read_file(path)

        if not content:
            continue

        if query_lower not in content.lower():
            continue

        lines = content.splitlines()

        matches = []

        for number, line in enumerate(lines, start=1):
            if query_lower in line.lower():
                matches.append({
                    "line": number,
                    "text": line.strip()[:300],
                })

                if len(matches) >= 5:
                    break

        results.append({
            "file": path,
            "matches": matches,
        })

        if len(results) >= limit:
            break

    return results


# ============================================================
# FILE SELECTION
# ============================================================

def select_relevant_files(task):
    """
    Memilih source code yang paling relevan.

    File inti untuk domain tertentu diprioritaskan agar tidak
    tersingkir hanya karena file lain memiliki skor tinggi.
    """

    index = load_index()

    task_words = {
        word.lower()
        for word in re.findall(r"[A-Za-z0-9_/-]+", task)
        if len(word) >= 3
    }

    scored = []

    for item in index.get("files", []):
        path = item["path"]
        path_lower = path.lower()

        score = 0

        for word in task_words:
            if word in path_lower:
                score += 10

        important_terms = {
            "rpc.rs": 20,
            "rpc_client.rs": 15,
            "tests/rpc.rs": 15,
            "node.rs": 15,
            "block": 5,
            "chain": 5,
            "state": 5,
            "mempool": 5,
            "transaction": 5,
            "crypto": 3,
            "execution": 3,
        }

        for term, value in important_terms.items():
            if term in path_lower:
                score += value

        if score > 0:
            scored.append((score, path))

    scored.sort(
        key=lambda item: (-item[0], item[1])
    )

    # --------------------------------------------------------
    # DOMAIN-SPECIFIC MUST INCLUDE FILES
    # --------------------------------------------------------

    task_lower = task.lower()

    mandatory = []

    if "rpc" in task_lower:
        mandatory = [
            "crates/synora-node/src/rpc.rs",
            "crates/synora-node/src/rpc_client.rs",
            "crates/synora-node/tests/rpc.rs",
            "crates/synora-node/src/node.rs",
        ]

    elif "transaction" in task_lower:
        mandatory = [
            "crates/synora-core/src/transaction/mod.rs",
            "crates/synora-core/src/mempool/mod.rs",
            "crates/synora-node/src/rpc.rs",
            "crates/synora-node/src/node.rs",
        ]

    elif "block" in task_lower:
        mandatory = [
            "crates/synora-core/src/block/mod.rs",
            "crates/synora-core/src/chain/mod.rs",
            "crates/synora-node/src/node.rs",
            "crates/synora-node/src/rpc.rs",
        ]

    elif "state" in task_lower:
        mandatory = [
            "crates/synora-core/src/state.rs",
            "crates/synora-node/src/node.rs",
            "crates/synora-node/src/rpc.rs",
        ]

    # --------------------------------------------------------
    # FINAL ORDER
    # --------------------------------------------------------

    selected = []

    # Mandatory files always come first.
    for path in mandatory:
        if path in index.get("path_map", {}) or any(
            item["path"] == path
            for item in index.get("files", [])
        ):
            if path not in selected:
                selected.append(path)

    # Fill remaining slots using relevance score.
    for _, path in scored:
        if path not in selected:
            selected.append(path)

        if len(selected) >= 12:
            break

    return selected[:12]


# ============================================================
# CONTEXT
# ============================================================

def build_context(task, files):
    """
    Membangun context berdasarkan prioritas file.

    File yang tidak muat karena budget dilewati, bukan menghentikan
    seluruh proses. Dengan begitu file penting setelah file besar
    tetap dapat masuk ke context.
    """

    task_lower = task.lower()

    # File yang sangat penting untuk domain tertentu.
    priority_rules = {
        "rpc": [
            "crates/synora-node/src/rpc.rs",
            "crates/synora-node/src/rpc_client.rs",
            "crates/synora-node/tests/rpc.rs",
            "crates/synora-node/src/node.rs",
        ],
        "transaction": [
            "crates/synora-core/src/transaction/mod.rs",
            "crates/synora-node/src/rpc.rs",
            "crates/synora-node/src/rpc_client.rs",
            "crates/synora-core/src/mempool/mod.rs",
        ],
        "block": [
            "crates/synora-core/src/block/mod.rs",
            "crates/synora-core/src/chain/mod.rs",
            "crates/synora-node/src/rpc.rs",
            "crates/synora-node/src/node.rs",
        ],
        "state": [
            "crates/synora-core/src/state.rs",
            "crates/synora-node/src/rpc.rs",
            "crates/synora-node/src/node.rs",
        ],
    }

    priority_files = []

    for keyword, paths in priority_rules.items():
        if keyword in task_lower:
            priority_files.extend(paths)

    # Buang duplikat tetapi pertahankan urutan.
    ordered_files = []

    for item in priority_files + list(files):
        if item not in ordered_files:
            ordered_files.append(item)

    chunks = []
    total = 0
    included = set()

    for relative_path in ordered_files:
        content = read_file(relative_path)

        if content is None:
            continue

        chunk = (
            f"\\n"
            f"===== FILE: {relative_path} =====\\n"
            f"{content}\\n"
            f"===== END FILE =====\\n"
        )

        chunk_size = len(chunk)

        # Jangan masukkan file jika melebihi budget tersisa.
        # Tetapi JANGAN break: lanjutkan ke file berikutnya.
        if total + chunk_size > MAX_CONTEXT_CHARS:
            continue

        chunks.append(chunk)
        total += chunk_size
        included.add(relative_path)

    print(f"Context files: {len(included)}")
    print(f"Context size: {total:,} karakter")

    return "".join(chunks)


# ============================================================
# GEMINI
# ============================================================

SYSTEM_PROMPT = """
Kamu adalah Synora Coding Agent.

Kamu membantu mengembangkan project blockchain bernama Synora.

Project:
- Rust
- Cargo workspace
- synora-core
- synora-node
- blockchain
- block
- chain
- state
- mempool
- transaction
- HTTP RPC

ATURAN PALING PENTING:

1. Jangan mengarang API, enum, struct, function, field, atau file.
2. Semua kesimpulan harus berdasarkan source code yang diberikan.
3. Jika source tidak cukup, katakan bahwa informasinya belum cukup.
4. Jangan menganggap nama enum dari contoh umum Rust sebagai enum yang benar.
5. Perhatikan nama variant yang benar-benar ada di source.
6. Jangan menghapus fitur existing tanpa alasan kuat.
7. Prioritaskan correctness dan security.
8. Untuk perubahan Rust, pikirkan cargo fmt, cargo check, dan cargo test.
9. Jangan memberikan perubahan besar jika perubahan kecil sudah cukup.
10. Jangan mengubah file yang tidak berkaitan.
11. Jangan menganggap backup directory sebagai source aktif.
12. Jangan memasukkan file .git, target, .venv, atau .synora-agent sebagai source project.
13. Context yang diberikan adalah source aktual. Gunakan itu sebagai sumber utama.
14. Jawaban harus konkret dan tidak bertele-tele.

Jika diminta ANALISIS:
- Jelaskan fakta.
- Jelaskan bug/potensi bug.
- Sebutkan file dan lokasi.
- Jangan mengubah kode.

Jika diminta PERBAIKAN:
- Buat rencana perubahan.
- Sebutkan file yang diubah.
- Jelaskan alasan.
- Jangan mengarang kode yang tidak didukung source.

Jika diminta menghasilkan patch:
- Gunakan path file yang benar.
- Berikan isi file lengkap hanya jika diminta.
- Jangan menghilangkan bagian existing.

Memory project juga diberikan sebagai konteks tambahan.
"""


def ask_gemini(task, context):
    memory = memory_context()

    prompt = f"""
{SYSTEM_PROMPT}

===== PROJECT MEMORY =====
{memory}

===== USER TASK =====
{task}

===== SOURCE CODE =====
{context}

Berikan jawaban berdasarkan source aktual di atas.
"""

    chat = client.chats.create(
        model=MODEL,
    )

    response = gemini_send_with_retry(chat, prompt)

    return response.text or ""


# ============================================================
# CHANGE PLAN
# ============================================================

def ask_change_plan(task, context):
    memory = memory_context()

    prompt = f"""
{SYSTEM_PROMPT}

Kamu sekarang bertugas membuat RENCANA PERUBAHAN.

User meminta:
{task}

Project memory:
{memory}

Source:
{context}

Buat jawaban dalam format:

PLAN
1. ...
2. ...

FILES
- path/file.rs
- path/file2.rs

REASON
...

RISKS
...

Jangan menulis perubahan file dulu.
Jangan mengarang API.
"""

    chat = client.chats.create(
        model=MODEL,
    )

    response = gemini_send_with_retry(chat, prompt)

    return response.text or ""


# ============================================================
# PATCH GENERATION
# ============================================================


def gemini_send_with_retry(chat, prompt, attempts=4):
    """
    Kirim request ke Gemini dengan retry hanya untuk error sementara.

    Error quota 429 tidak di-retry karena retry tidak akan
    memperbaiki quota harian dan justru menghabiskan request.
    """
    import time

    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            return chat.send_message(prompt)

        except Exception as error:
            last_error = error
            error_text = str(error)

            print(
                f"Gemini request gagal "
                f"(attempt {attempt}/{attempts}): {error}"
            )

            # Quota/rate-limit bukan transient connection error.
            # Jangan menghabiskan request tambahan.
            if (
                "429" in error_text
                or "RESOURCE_EXHAUSTED" in error_text
                or "quota" in error_text.lower()
            ):
                raise RuntimeError(
                    "Gemini API quota/rate limit tercapai. "
                    "Retry dihentikan agar tidak membuang request."
                ) from error

            if attempt < attempts:
                delay = min(2 ** (attempt - 1), 8)

                print(
                    f"Menunggu {delay} detik sebelum retry..."
                )

                time.sleep(delay)

    raise RuntimeError(
        f"Gemini gagal setelah {attempts} percobaan: {last_error}"
    )

def ask_patch(task, context, plan):
    prompt = f"""
{SYSTEM_PROMPT}

Kamu harus membuat perubahan kode berdasarkan rencana berikut.

===== USER TASK =====
{task}

===== PLAN =====
{plan}

===== SOURCE =====
{context}

Kembalikan JSON VALID SAJA dengan format:

{{
  "changes": [
    {{
      "path": "relative/path/file.rs",
      "content": "FULL CONTENT OF FILE"
    }}
  ],
  "summary": "ringkasan perubahan"
}}

ATURAN:
- Hanya file yang benar-benar perlu diubah.
- Path harus relatif terhadap root project.
- Content harus isi FILE LENGKAP.
- Jangan menggunakan markdown fence.
- Jangan menambahkan komentar di luar JSON.
- Jangan mengarang file.
- Jangan mengubah file test/build/generated jika tidak diperlukan.
"""

    chat = client.chats.create(
        model=MODEL,
    )

    response = gemini_send_with_retry(chat, prompt)

    text = response.text or ""

    # Normalisasi output Gemini.
    text = text.strip()

    # Jika Gemini membungkus JSON dengan markdown fence.
    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\\s*```$",
            "",
            text,
        ).strip()

    # Jika Gemini menambahkan teks sebelum/sesudah JSON,
    # ambil object JSON terluar.
    if not text.startswith("{"):
        json_start = text.find("{")
        json_end = text.rfind("}")

        if json_start != -1 and json_end > json_start:
            text = text[json_start:json_end + 1]

    try:
        patch = json.loads(text)

    except json.JSONDecodeError as error:
        print()
        print("Gemini tidak menghasilkan JSON patch yang valid.")
        print(f"Parser error: {error}")
        print(text)
        return None

    if not isinstance(patch, dict):
        print()
        print("Patch Gemini bukan JSON object.")
        return None

    changes = patch.get("changes")

    if not isinstance(changes, list):
        print()
        print("Patch Gemini tidak memiliki field 'changes' yang valid.")
        return None

    return patch



# ============================================================
# SAFETY
# ============================================================

def safe_project_path(relative_path):
    if not relative_path:
        return None

    path = (ROOT / relative_path).resolve()

    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return None

    if any(part in IGNORED_DIRS for part in path.relative_to(ROOT).parts):
        return None

    return path


def validate_changes(changes):
    if not isinstance(changes, list):
        return False, "changes bukan list"

    if len(changes) == 0:
        return False, "tidak ada perubahan"

    if len(changes) > 8:
        return False, "terlalu banyak file diubah sekaligus"

    for change in changes:
        if not isinstance(change, dict):
            return False, "format perubahan tidak valid"

        relative_path = change.get("path")
        content = change.get("content")

        if not isinstance(relative_path, str):
            return False, "path tidak valid"

        if not isinstance(content, str):
            return False, f"content tidak valid: {relative_path}"

        path = safe_project_path(relative_path)

        if path is None:
            return False, f"path berbahaya/tidak valid: {relative_path}"

        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            return False, f"file terlalu besar: {relative_path}"

    return True, "ok"


# ============================================================
# GIT SAFETY
# ============================================================

PROTECTED_PATHS = {
    ".env",
    ".git",
}

PROTECTED_PREFIXES = (
    ".git/",
    ".synora-agent/",
    ".venv/",
    "target/",
)


def normalize_project_path(relative_path):
    """
    Normalisasi path relatif tanpa merusak nama hidden file
    seperti .env dan .git.
    """

    if not isinstance(relative_path, str):
        return ""

    normalized = relative_path.replace("\\", "/")

    while normalized.startswith("./"):
        normalized = normalized[2:]

    return normalized


def is_protected_path(relative_path):
    """
    Menolak perubahan terhadap path sensitif.
    """

    normalized = normalize_project_path(relative_path)

    if normalized in PROTECTED_PATHS:
        return True

    if normalized.startswith(PROTECTED_PREFIXES):
        return True

    if normalized.endswith("/.env"):
        return True

    return False


def validate_git_changes(changes):
    """
    Validasi tambahan untuk perubahan yang dihasilkan Gemini.
    """

    if not isinstance(changes, list):
        return False, "changes bukan list"

    seen = set()

    for change in changes:
        if not isinstance(change, dict):
            return False, "format perubahan tidak valid"

        relative_path = change.get("path")

        if not isinstance(relative_path, str):
            return False, "path perubahan bukan string"

        normalized = normalize_project_path(relative_path)

        if not normalized:
            return False, "path kosong"

        if normalized in seen:
            return False, f"duplicate path: {normalized}"

        seen.add(normalized)

        if is_protected_path(normalized):
            return False, (
                f"path protected tidak boleh diubah: "
                f"{normalized}"
            )

    return True, "ok"

# ============================================================
# GIT STATE BASELINE
# ============================================================

def git_status_paths():
    """
    Mengambil seluruh path yang berubah menurut Git.

    Menggunakan porcelain format agar aman diproses program.
    Tidak melakukan perubahan apa pun terhadap working tree.
    """

    try:
        result = subprocess.run(
            [
                "git",
                "status",
                "--short",
                "--untracked-files=all",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )

    except Exception as error:
        raise RuntimeError(
            f"Gagal membaca Git status: {error}"
        ) from error

    if result.returncode != 0:
        raise RuntimeError(
            "Git status gagal:\n"
            f"{result.stdout}"
        )

    paths = set()

    for line in result.stdout.splitlines():
        if not line.strip():
            continue

        entry = line[3:]

        if " -> " in entry:
            old_path, new_path = entry.split(
                " -> ",
                1,
            )

            paths.add(
                normalize_project_path(old_path)
            )

            paths.add(
                normalize_project_path(new_path)
            )

        else:
            paths.add(
                normalize_project_path(entry)
            )

    return sorted(
        path
        for path in paths
        if path
    )


def git_baseline():
    """
    Mengambil baseline Git sebelum agent melakukan perubahan.

    Baseline hanya berupa daftar path yang sudah berubah.
    Tidak mengubah repository.
    """

    paths = git_status_paths()

    return {
        "paths": paths,
    }



def verify_git_changes(baseline, changes):
    """
    Memastikan agent hanya menghasilkan perubahan pada target
    yang memang diizinkan.

    Perubahan Git yang sudah ada sebelum agent bekerja dianggap
    sebagai baseline user dan tidak dianggap sebagai perubahan
    liar.
    """

    if not isinstance(baseline, dict):
        return False, "baseline Git tidak valid"

    baseline_paths = set(
        baseline.get("paths", [])
    )

    expected_paths = set()

    for change in changes:
        relative_path = normalize_project_path(
            change.get("path", "")
        )

        if relative_path:
            expected_paths.add(relative_path)

    current_paths = set(
        git_status_paths()
    )

    new_paths = current_paths - baseline_paths

    unexpected_paths = new_paths - expected_paths

    if unexpected_paths:
        return False, (
            "perubahan Git tidak terduga: "
            + ", ".join(
                sorted(unexpected_paths)
            )
        )

    return True, "ok"


def snapshot_files(changes):
    """
    Membuat snapshot state file sebelum agent melakukan perubahan.

    Snapshot hanya mencakup file yang memang akan diubah agent.
    Perubahan file lain di working tree tidak disentuh.
    """

    snapshots = []

    for change in changes:
        relative_path = normalize_project_path(
            change.get("path", "")
        )

        path = safe_project_path(relative_path)

        if path is None:
            raise RuntimeError(
                f"Path snapshot tidak aman: {relative_path}"
            )

        existed = path.exists()

        content = None

        if existed:
            if not path.is_file():
                raise RuntimeError(
                    f"Target bukan file biasa: {relative_path}"
                )

            try:
                content = path.read_bytes()
            except OSError as error:
                raise RuntimeError(
                    f"Gagal membaca snapshot: "
                    f"{relative_path}: {error}"
                ) from error

        snapshots.append({
            "path": relative_path,
            "existed": existed,
            "content": content,
        })

    return snapshots


def verify_snapshot_targets(changes):
    """
    Memastikan snapshot target valid sebelum apply.
    """

    seen = set()

    for change in changes:
        relative_path = normalize_project_path(
            change.get("path", "")
        )

        if not relative_path:
            return False, "snapshot memiliki path kosong"

        if relative_path in seen:
            return False, (
                f"duplicate snapshot path: {relative_path}"
            )

        seen.add(relative_path)

        if is_protected_path(relative_path):
            return False, (
                f"snapshot mencoba protected path: "
                f"{relative_path}"
            )

        if safe_project_path(relative_path) is None:
            return False, (
                f"snapshot path tidak aman: "
                f"{relative_path}"
            )

    return True, "ok"


def restore_snapshots(snapshots):
    """
    Restore hanya file yang tercatat dalam snapshot.

    Tidak menggunakan:
    - git reset
    - git checkout
    - git clean
    - git stash

    Dengan demikian perubahan user di file lain tetap utuh.
    """

    print()
    print("=" * 60)
    print("RESTORE SNAPSHOT")
    print("=" * 60)

    for snapshot in reversed(snapshots):
        relative_path = snapshot["path"]
        path = safe_project_path(relative_path)

        if path is None:
            raise RuntimeError(
                f"Restore path tidak aman: {relative_path}"
            )

        existed = snapshot["existed"]
        content = snapshot["content"]

        if existed:
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            path.write_bytes(content)

            print(
                f"✓ Restore: {relative_path}"
            )

        else:
            if path.exists():
                if path.is_file():
                    path.unlink()

                    print(
                        f"✓ Hapus file baru: "
                        f"{relative_path}"
                    )
                else:
                    raise RuntimeError(
                        f"Tidak dapat menghapus target "
                        f"non-file: {relative_path}"
                    )

    print("=" * 60)


def snapshot_changed_files(snapshots):
    """
    Memverifikasi apakah target snapshot berubah.
    """

    changed = []

    for snapshot in snapshots:
        relative_path = snapshot["path"]
        path = safe_project_path(relative_path)

        if path is None:
            changed.append(relative_path)
            continue

        existed_before = snapshot["existed"]

        if not existed_before:
            if path.exists():
                changed.append(relative_path)

            continue

        if not path.exists():
            changed.append(relative_path)
            continue

        if not path.is_file():
            changed.append(relative_path)
            continue

        try:
            current = path.read_bytes()
        except OSError:
            changed.append(relative_path)
            continue

        if current != snapshot["content"]:
            changed.append(relative_path)

    return changed


def git_diff_for_paths(paths):
    """
    Mengambil diff Git untuk file existing.
    """

    if not paths:
        return ""

    try:
        result = subprocess.run(
            ["git", "diff", "--"] + paths,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )

        return result.stdout

    except Exception as error:
        return f"[GIT DIFF ERROR] {error}"


def show_git_diff(changes):
    """
    Menampilkan preview perubahan existing sebelum apply.
    """

    paths = []

    for change in changes:
        relative_path = change.get("path")

        if isinstance(relative_path, str):
            paths.append(relative_path)

    if not paths:
        return

    diff = git_diff_for_paths(paths)

    print()
    print("=" * 60)
    print("GIT DIFF PREVIEW")
    print("=" * 60)

    if diff:
        print(diff)
    else:
        print(
            "File existing belum memiliki Git diff. "
            "File baru akan terlihat setelah apply."
        )

    print("=" * 60)


# ============================================================
# BACKUP
# ============================================================

def backup_file(path):
    backup_dir = AGENT_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    relative = path.relative_to(ROOT)

    backup_path = (
        backup_dir
        / f"{timestamp}__{str(relative).replace('/', '__')}"
    )

    if path.exists():
        backup_path.write_bytes(path.read_bytes())

    return backup_path


# ============================================================
# WRITE FILES
# ============================================================

def apply_changes(changes):
    valid, message = validate_changes(changes)

    if not valid:
        raise RuntimeError(
            f"Perubahan ditolak: {message}"
        )

    applied = []

    for change in changes:
        relative_path = change["path"]
        content = change["content"]

        path = safe_project_path(relative_path)

        if path is None:
            raise RuntimeError(
                f"Path tidak aman: {relative_path}"
            )

        existed = path.exists()

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        backup = backup_file(path)

        path.write_text(
            content,
            encoding="utf-8",
        )

        applied.append({
            "path": relative_path,
            "backup": (
                str(backup.relative_to(ROOT))
                if existed
                else None
            ),
            "existed": existed,
        })

    return applied


# ============================================================
# ROLLBACK
# ============================================================

def rollback_changes(applied):
    """
    Mengembalikan semua perubahan yang sudah diterapkan.

    - File yang sebelumnya ada -> restore dari backup.
    - File baru -> hapus.
    """

    if not applied:
        print("Tidak ada perubahan untuk di-rollback.")
        return

    print()
    print("=" * 60)
    print("ROLLBACK PERUBAHAN")
    print("=" * 60)

    for item in reversed(applied):
        relative_path = item["path"]
        existed = item.get("existed", False)
        backup_relative = item.get("backup")

        path = safe_project_path(relative_path)

        if path is None:
            print(
                f"✗ Rollback ditolak untuk path: "
                f"{relative_path}"
            )
            continue

        try:
            if existed:
                if not backup_relative:
                    print(
                        f"✗ Backup tidak ditemukan: "
                        f"{relative_path}"
                    )
                    continue

                backup_path = ROOT / backup_relative

                if not backup_path.exists():
                    print(
                        f"✗ File backup tidak ditemukan: "
                        f"{backup_relative}"
                    )
                    continue

                path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                path.write_bytes(
                    backup_path.read_bytes()
                )

                print(
                    f"✓ Restore: {relative_path}"
                )

            else:
                if path.exists():
                    path.unlink()

                print(
                    f"✓ Hapus file baru: "
                    f"{relative_path}"
                )

        except Exception as error:
            print(
                f"✗ Rollback gagal untuk "
                f"{relative_path}: {error}"
            )

    print("=" * 60)


# ============================================================
# COMMAND EXECUTION
# ============================================================

def run_safe_command(command):
    if command not in SAFE_COMMANDS:
        raise RuntimeError(
            f"Command tidak diizinkan: {command}"
        )

    print()
    print(f"$ {command}")
    print("-" * 60)

    result = subprocess.run(
        command,
        cwd=ROOT,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )

    output = result.stdout

    print(output)

    print("-" * 60)

    if result.returncode == 0:
        print(f"✓ {command} berhasil")
    else:
        print(
            f"✗ {command} gagal "
            f"(exit {result.returncode})"
        )

    return result.returncode, output


# ============================================================
# ANALYZE MODE
# ============================================================

def analyze(task):
    print("Synora AI Agent")
    print(f"Model: {MODEL}")
    print(f"Project: {ROOT}")
    print(f"Task: {task}")

    print()
    print("Menganalisis file yang relevan...")

    files = select_relevant_files(task)

    print(
        f"Source terpilih: {len(files)} file"
    )

    for path in files:
        print(f"  - {path}")

    context = build_context(
        task,
        files,
    )

    print()
    print(
        f"Context: {len(context):,} karakter"
    )

    print()
    print("Mengirim context ke Gemini...")

    result = ask_gemini(
        task,
        context,
    )

    print()
    print("===== SYNORA AI =====")
    print(result)

    update_memory(
        task,
        result,
    )

    append_history(
        "analysis",
        {
            "task": task,
            "files": files,
        },
    )


# ============================================================
# FIX MODE
# ============================================================

def fix(task):
    print("Synora AI Coding Agent")
    print(f"Model: {MODEL}")
    print(f"Project: {ROOT}")
    print(f"Task: {task}")

    files = select_relevant_files(task)

    print()
    print(
        f"Source terpilih: {len(files)} file"
    )

    for path in files:
        print(f"  - {path}")

    context = build_context(
        task,
        files,
    )

    print()
    print("Menganalisis rencana perubahan...")

    plan = ask_change_plan(
        task,
        context,
    )

    print()
    print("===== CHANGE PLAN =====")
    print(plan)

    print()
    answer = input(
        "Lanjut membuat perubahan kode? [y/N]: "
    ).strip().lower()

    if answer != "y":
        print("Dibatalkan. Tidak ada file yang diubah.")
        return

    print()
    print("Membuat patch...")

    patch = ask_patch(
        task,
        context,
        plan,
    )

    if not patch:
        print("Patch gagal dibuat.")
        return

    changes = patch.get("changes")

    valid, message = validate_changes(
        changes
    )

    if not valid:
        print(
            f"Patch ditolak: {message}"
        )
        return

    git_valid, git_message = validate_git_changes(
        changes
    )

    if not git_valid:
        print(
            f"Patch ditolak oleh Git safety: "
            f"{git_message}"
        )
        return

    print()
    print("===== FILE YANG AKAN DIUBAH =====")

    for change in changes:
        print(
            f"- {change['path']}"
        )

    show_git_diff(changes)

    print()
    answer = input(
        "Terapkan perubahan? [y/N]: "
    ).strip().lower()

    if answer != "y":
        print("Dibatalkan.")
        return

    print()
    print("Memvalidasi target snapshot...")

    snapshot_valid, snapshot_message = verify_snapshot_targets(
        changes
    )

    if not snapshot_valid:
        print(
            f"Snapshot ditolak: {snapshot_message}"
        )
        return

    print(
        f"✓ Target snapshot valid: {len(changes)} file."
    )

    print()
    print("Membaca Git baseline sebelum apply...")

    try:
        git_baseline_state = git_baseline()
    except Exception as error:
        print(
            f"✗ Git baseline gagal: {error}"
        )
        return

    print(
        f"✓ Git baseline: "
        f"{len(git_baseline_state.get('paths', []))} path."
    )

    print()
    print("Membuat snapshot sebelum apply...")

    try:
        snapshots = snapshot_files(changes)
    except Exception as error:
        print(
            f"✗ Snapshot gagal: {error}"
        )
        return

    print(
        f"✓ Snapshot {len(snapshots)} file berhasil dibuat."
    )

    print()
    print("Menerapkan perubahan...")

    try:
        applied = apply_changes(
            changes
        )
    except Exception as error:
        print()
        print(
            f"✗ Apply gagal: {error}"
        )
        print(
            "Mengembalikan snapshot..."
        )

        try:
            restore_snapshots(snapshots)
        except Exception as restore_error:
            print(
                f"✗ RESTORE GAGAL: {restore_error}"
            )
            raise

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "apply failed",
                "files": [
                    snapshot["path"]
                    for snapshot in snapshots
                ],
            },
        )

        return

    for item in applied:
        print(
            f"✓ {item['path']}"
        )
        print(
            f"  backup: {item['backup']}"
        )

    append_history(
        "files_changed",
        {
            "task": task,
            "files": applied,
        },
    )

    print()
    print("Memverifikasi Git state setelah apply...")

    git_valid, git_message = verify_git_changes(
        git_baseline_state,
        changes,
    )

    if not git_valid:
        print(
            f"✗ Git state tidak aman: {git_message}"
        )
        print(
            "Perubahan akan di-rollback..."
        )

        try:
            restore_snapshots(snapshots)
        except Exception as restore_error:
            print(
                f"✗ RESTORE GAGAL: {restore_error}"
            )
            raise

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "unexpected git state after apply",
                "files": [
                    snapshot["path"]
                    for snapshot in snapshots
                ],
            },
        )

        return

    print("✓ Git state setelah apply aman.")

    print()
    print("Menjalankan cargo fmt...")

    fmt_code, _ = run_safe_command(
        "cargo fmt --all"
    )

    if fmt_code != 0:
        print(
            "cargo fmt gagal. "
            "Mengembalikan perubahan..."
        )

        restore_snapshots(snapshots)

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "cargo fmt failed",
                "files": applied,
            },
        )

        return

    print()
    print("Memverifikasi Git state setelah cargo fmt...")

    git_valid, git_message = verify_git_changes(
        git_baseline_state,
        changes,
    )

    if not git_valid:
        print(
            f"✗ Git state berubah secara tak terduga: "
            f"{git_message}"
        )
        print(
            "Perubahan akan di-rollback..."
        )

        restore_snapshots(snapshots)

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "unexpected git state after cargo fmt",
                "files": applied,
            },
        )

        return

    print("✓ Git state setelah cargo fmt aman.")

    print()
    print("Menjalankan cargo check...")

    check_code, check_output = run_safe_command(
        "cargo check"
    )

    if check_code != 0:
        print()
        print(
            "cargo check gagal."
        )
        print(
            "Perubahan akan di-rollback..."
        )

        restore_snapshots(snapshots)

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "cargo check failed",
                "files": applied,
            },
        )

        return

    print()
    print("Memverifikasi Git state setelah cargo check...")

    git_valid, git_message = verify_git_changes(
        git_baseline_state,
        changes,
    )

    if not git_valid:
        print(
            f"✗ Git state berubah secara tak terduga: "
            f"{git_message}"
        )
        print(
            "Perubahan akan di-rollback..."
        )

        restore_snapshots(snapshots)

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "unexpected git state after cargo check",
                "files": applied,
            },
        )

        return

    print("✓ Git state setelah cargo check aman.")

    print()
    print("Menjalankan cargo test...")

    test_code, test_output = run_safe_command(
        "cargo test"
    )

    if test_code != 0:
        print()
        print(
            "cargo test gagal."
        )
        print(
            "Perubahan akan di-rollback..."
        )

        restore_snapshots(snapshots)

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "cargo test failed",
                "files": applied,
            },
        )

        return

    print()
    print("Memverifikasi Git state setelah cargo test...")

    git_valid, git_message = verify_git_changes(
        git_baseline_state,
        changes,
    )

    if not git_valid:
        print(
            f"✗ Git state berubah secara tak terduga: "
            f"{git_message}"
        )
        print(
            "Perubahan akan di-rollback..."
        )

        restore_snapshots(snapshots)

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "unexpected git state after cargo test",
                "files": applied,
            },
        )

        return

    print("✓ Git state setelah cargo test aman.")

    print()
    print("Memverifikasi Git state final transaction...")

    git_valid, git_message = verify_git_changes(
        git_baseline_state,
        changes,
    )

    if not git_valid:
        print()
        print(
            "✗ Final Git verification gagal: "
            f"{git_message}"
        )

        print(
            "Perubahan transaction akan di-rollback..."
        )

        try:
            restore_snapshots(snapshots)
        except Exception as restore_error:
            print(
                f"✗ RESTORE GAGAL: {restore_error}"
            )
            raise

        append_history(
            "fix_rolled_back",
            {
                "task": task,
                "reason": "final git verification failed",
                "message": git_message,
                "files": [
                    snapshot["path"]
                    for snapshot in snapshots
                ],
            },
        )

        return

    print(
        "✓ Final Git state aman."
    )

    summary = patch.get(
        "summary",
        "Perubahan berhasil diterapkan.",
    )

    update_memory(
        task,
        summary,
    )

    append_history(
        "fix_completed",
        {
            "task": task,
            "files": applied,
            "summary": summary,
        },
    )

    print()
    print("=" * 60)
    print("✓ PERUBAHAN SELESAI")
    print("=" * 60)


# ============================================================
# STATUS
# ============================================================

def show_status():
    index = load_index()
    memory = load_memory()

    print_header("SYNORA AGENT STATUS")

    print(f"Project : {ROOT}")
    print(f"Model   : {MODEL}")
    print(
        f"Indexed : "
        f"{index.get('file_count', 0)} files"
    )

    print()
    print("Memory:")

    print(
        f"  Facts       : "
        f"{len(memory.get('facts', []))}"
    )

    print(
        f"  Decisions   : "
        f"{len(memory.get('decisions', []))}"
    )

    print(
        f"  Issues      : "
        f"{len(memory.get('known_issues', []))}"
    )

    print(
        f"  Completed   : "
        f"{len(memory.get('completed_tasks', []))}"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    ensure_agent_dir()

    if len(sys.argv) < 2:
        print(
            """
Synora AI Agent

Usage:

  python agent/main.py --index

      Bangun ulang project index.

  python agent/main.py --status

      Lihat status agent dan memory.

  python agent/main.py "pertanyaan"

      Analisis project.

  python agent/main.py --fix "tugas"

      Analisis dan usulkan perubahan kode.
      Agent meminta konfirmasi sebelum menulis.

Contoh:

  python agent/main.py \
    "jelaskan arsitektur RPC Synora"

  python agent/main.py \
    "cek bug pada POST /transaction"

  python agent/main.py --fix \
    "perbaiki timeout pada RPC server"

  python agent/main.py --fix \
    "tambahkan test untuk prefix 0x pada address"
"""
        )
        return

    command = sys.argv[1]

    if command == "--index":
        build_index()
        return

    if command == "--status":
        show_status()
        return

    if command == "--fix":
        if len(sys.argv) < 3:
            raise SystemExit(
                "Task untuk --fix wajib diberikan."
            )

        task = " ".join(sys.argv[2:])
        fix(task)
        return

    task = " ".join(sys.argv[1:])

    analyze(task)


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print()
        print("Agent dihentikan.")

    except subprocess.TimeoutExpired:
        print()
        print(
            "Command timeout."
        )

    except Exception as error:
        print()
        print(
            f"ERROR: {type(error).__name__}: {error}"
        )
        raise
