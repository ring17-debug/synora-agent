#!/usr/bin/env python3

"""
Synora Agent Intelligence V2

Role-based LLM orchestration layer.

Roles:
    planner
    coder
    reviewer
    tester
    debugger

Modul ini TIDAK mengubah Git, filesystem, snapshot,
atau transaction guard. Ia hanya mengatur:
    task -> role -> prompt -> LLM response
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


# ============================================================
# ROLE DEFINITIONS
# ============================================================

@dataclass(frozen=True)
class AgentRole:
    name: str
    description: str
    responsibilities: tuple
    allowed_output: str


ROLES: Dict[str, AgentRole] = {
    "planner": AgentRole(
        name="planner",
        description=(
            "Menganalisis masalah dan membuat rencana "
            "implementasi yang konkret."
        ),
        responsibilities=(
            "memahami task",
            "mengidentifikasi file relevan",
            "menentukan dependency antar perubahan",
            "mengidentifikasi risiko",
            "membuat urutan implementasi",
        ),
        allowed_output="structured implementation plan",
    ),

    "coder": AgentRole(
        name="coder",
        description=(
            "Mengimplementasikan perubahan kode berdasarkan "
            "rencana dan source aktual."
        ),
        responsibilities=(
            "mengubah source code",
            "mempertahankan API existing",
            "menghindari perubahan tidak relevan",
            "menghasilkan full-file changes",
            "memastikan perubahan konsisten",
        ),
        allowed_output="structured JSON patch",
    ),

    "reviewer": AgentRole(
        name="reviewer",
        description=(
            "Melakukan review terhadap perubahan kode "
            "sebelum testing."
        ),
        responsibilities=(
            "memeriksa correctness",
            "memeriksa security",
            "memeriksa regression",
            "memeriksa API compatibility",
            "memeriksa scope perubahan",
        ),
        allowed_output="structured review",
    ),

    "tester": AgentRole(
        name="tester",
        description=(
            "Merancang dan mengevaluasi pengujian "
            "terhadap perubahan."
        ),
        responsibilities=(
            "menentukan test yang relevan",
            "memeriksa build",
            "memeriksa unit test",
            "memeriksa integration test",
            "menentukan expected result",
        ),
        allowed_output="structured test plan",
    ),

    "debugger": AgentRole(
        name="debugger",
        description=(
            "Menganalisis kegagalan command atau test "
            "dan mencari akar masalah."
        ),
        responsibilities=(
            "menganalisis error",
            "menghubungkan error dengan source",
            "mencari root cause",
            "menentukan perbaikan minimal",
            "menghindari speculative fixes",
        ),
        allowed_output="structured diagnosis",
    ),
}


# ============================================================
# PIPELINE
# ============================================================

DEFAULT_PIPELINE = (
    "planner",
    "coder",
    "reviewer",
    "tester",
)


DEBUG_PIPELINE = (
    "planner",
    "coder",
    "reviewer",
    "tester",
    "debugger",
)


# ============================================================
# ROUTING
# ============================================================

# ============================================================
# ROUTING RULES
# ============================================================

# Rule routing dibagi berdasarkan specificity.
#
# Keyword umum seperti:
#   buat, tambah, ubah, RPC
#
# tidak boleh mengalahkan intent yang lebih spesifik seperti:
#   buat test
#   review security
#   debug cargo test
#
# Semakin besar weight, semakin spesifik intent tersebut.

ROUTE_RULES = {
    "planner": (
        ("analisis implementasi", 8),
        ("rencana perubahan", 8),
        ("buat rencana", 8),
        ("rencana", 6),
        ("arsitektur", 6),
        ("design", 6),
        ("desain", 6),
        ("plan", 5),
    ),

    "coder": (
        ("implementasikan", 8),
        ("implementasi", 7),
        ("perbaiki kode", 7),
        ("tambahkan fitur", 7),
        ("buat endpoint", 7),
        ("buat function", 7),
        ("buat fungsi", 7),
        ("ubah kode", 6),
        ("tambahkan", 4),
        ("tambah", 3),
        ("buat", 2),
        ("endpoint", 2),
        ("rpc", 1),
        ("function", 2),
        ("fungsi", 2),
        ("fitur", 2),
    ),

    "reviewer": (
        ("review keamanan", 10),
        ("security review", 10),
        ("review code", 9),
        ("code review", 9),
        ("audit keamanan", 10),
        ("audit security", 10),
        ("review", 7),
        ("reviewer", 7),
        ("keamanan", 6),
        ("security", 6),
        ("audit", 6),
        ("cek kode", 5),
    ),

    "tester": (
        ("integration test", 10),
        ("unit test", 10),
        ("buat test", 10),
        ("buat testing", 10),
        ("buat pengujian", 10),
        ("tambah test", 10),
        ("tambahkan test", 10),
        ("testing", 8),
        ("pengujian", 8),
        ("test", 7),
    ),

    "debugger": (
        ("debug cargo test", 12),
        ("debug cargo check", 12),
        ("compile error", 11),
        ("cargo test gagal", 11),
        ("cargo check gagal", 11),
        ("debug", 10),
        ("bug", 9),
        ("error", 8),
        ("gagal", 8),
        ("failure", 8),
        ("panic", 8),
        ("cargo check", 5),
        ("cargo test", 5),
    ),
}


@dataclass(frozen=True)
class RouteDecision:
    role: str
    confidence: float
    reason: str


def route_task(task: str) -> RouteDecision:
    """
    Routing deterministic sebelum LLM.

    Tujuan:
    - predictable
    - mudah dites
    - tidak bergantung pada API
    - menghindari role conflict
    """

    if not isinstance(task, str):
        return RouteDecision(
            role="planner",
            confidence=0.50,
            reason="Task bukan string; menggunakan planner.",
        )

    text = task.lower().strip()

    if not text:
        return RouteDecision(
            role="planner",
            confidence=0.50,
            reason="Task kosong; menggunakan planner.",
        )

    scores = {
        role: 0
        for role in ROUTE_RULES
    }

    matched = {
        role: []
        for role in ROUTE_RULES
    }

    for role, rules in ROUTE_RULES.items():
        for keyword, weight in rules:
            if keyword in text:
                scores[role] += weight
                matched[role].append(keyword)

    best_role = max(
        scores,
        key=scores.get,
    )

    best_score = scores[best_role]

    if best_score == 0:
        return RouteDecision(
            role="planner",
            confidence=0.60,
            reason=(
                "Tidak ada intent spesifik; "
                "planner digunakan sebagai entry point."
            ),
        )

    confidence = min(
        0.99,
        0.70 + (best_score * 0.03),
    )

    keywords = ", ".join(
        matched[best_role]
    )

    return RouteDecision(
        role=best_role,
        confidence=confidence,
        reason=(
            f"Intent cocok dengan role {best_role}. "
            f"Weighted score: {best_score}. "
            f"Keyword: {keywords}."
        ),
    )


# ============================================================
# ROLE PROMPTS
# ============================================================

ROLE_PRINCIPLES = """
Kamu adalah salah satu role dalam Synora Agent.

ATURAN GLOBAL:

1. Source code yang diberikan adalah sumber kebenaran.
2. Jangan mengarang API.
3. Jangan mengarang struct, enum, function, field,
   module, crate, atau file.
4. Jika informasi tidak cukup, katakan dengan jelas.
5. Jangan mengubah bagian yang tidak berkaitan.
6. Pertahankan behavior existing kecuali task memang
   meminta perubahan behavior.
7. Prioritaskan correctness dan security.
8. Perubahan harus minimal tetapi lengkap.
9. Jangan menggunakan target/, .git/, .venv/,
   atau .synora-agent/ sebagai source aktif.
10. Jangan menganggap contoh umum Rust sebagai API
    project ini.
11. Semua keputusan harus dapat ditelusuri ke context.
"""


def build_role_prompt(
    role: str,
    task: str,
    context: str,
    previous_results: Optional[List[str]] = None,
) -> str:
    """
    Membuat prompt khusus berdasarkan role.
    """

    if role not in ROLES:
        raise ValueError(
            f"Role tidak dikenal: {role}"
        )

    role_info = ROLES[role]

    previous_results = previous_results or []

    previous_context = ""

    if previous_results:
        previous_context = (
            "\n\n===== PREVIOUS AGENT RESULTS =====\n"
            + "\n\n".join(previous_results)
        )

    responsibilities = "\n".join(
        f"- {item}"
        for item in role_info.responsibilities
    )

    return f"""
{ROLE_PRINCIPLES}

============================================================
ROLE
============================================================

Role:
{role_info.name}

Description:
{role_info.description}

Responsibilities:
{responsibilities}

Allowed output:
{role_info.allowed_output}

============================================================
USER TASK
============================================================

{task}

============================================================
SOURCE CONTEXT
============================================================

{context}

{previous_context}

============================================================
ROLE-SPECIFIC INSTRUCTIONS
============================================================
{role_instructions(role)}

Jawab hanya sesuai tanggung jawab role ini.
"""


def role_instructions(role: str) -> str:
    instructions = {
        "planner": """
Buat rencana implementasi.

Wajib mencakup:

PLAN
1. langkah pertama
2. langkah berikutnya

FILES
- file yang relevan

DEPENDENCIES
- dependency antar perubahan

RISKS
- risiko implementasi

VERIFICATION
- cara memverifikasi hasil

Jangan menghasilkan kode.
""",

        "coder": """
Implementasikan perubahan berdasarkan task dan context.

Wajib:

- hanya mengubah file yang diperlukan
- mempertahankan API existing
- tidak mengarang source
- memperhatikan error handling
- memperhatikan backward compatibility

Jika sistem meminta patch JSON, hasil akhir harus mengikuti
schema patch yang diberikan oleh caller.
""",

        "reviewer": """
Review perubahan secara kritis.

Periksa:

1. correctness
2. security
3. regression
4. API compatibility
5. error handling
6. edge cases
7. unnecessary changes

Gunakan format:

VERDICT
PASS atau FAIL

ISSUES
- issue 1
- issue 2

SEVERITY
LOW / MEDIUM / HIGH / CRITICAL

RECOMMENDATION
...
""",

        "tester": """
Tentukan pengujian yang diperlukan.

Periksa:

1. compile
2. formatter
3. unit test
4. integration test
5. regression test
6. edge case

Gunakan format:

TEST PLAN
1. ...

EXPECTED
...

FAIL CONDITIONS
...
""",

        "debugger": """
Cari root cause dari kegagalan.

Jangan langsung menebak.

Gunakan format:

SYMPTOM
...

ROOT CAUSE
...

EVIDENCE
...

FIX
...

VERIFICATION
...
""",
    }

    return instructions[role]


# ============================================================
# PIPELINE BUILDING
# ============================================================

def build_pipeline(
    task: str,
    include_debugger: bool = False,
) -> List[str]:
    """
    Menentukan pipeline berdasarkan task.
    """

    if include_debugger:
        return list(DEBUG_PIPELINE)

    decision = route_task(task)

    if decision.role == "debugger":
        return list(DEBUG_PIPELINE)

    return list(DEFAULT_PIPELINE)


def describe_pipeline(
    pipeline: List[str],
) -> List[str]:
    """
    Menghasilkan deskripsi pipeline untuk UI/log.
    """

    result = []

    for index, role in enumerate(pipeline, start=1):
        info = ROLES[role]

        result.append(
            f"{index}. {info.name} - "
            f"{info.description}"
        )

    return result


# ============================================================
# ORCHESTRATOR STATE
# ============================================================

@dataclass
class AgentRun:
    task: str
    route: RouteDecision
    pipeline: List[str]
    results: List[dict]

    def add_result(
        self,
        role: str,
        output: str,
    ):
        self.results.append({
            "role": role,
            "output": output,
        })

    def latest_result(self) -> Optional[dict]:
        if not self.results:
            return None

        return self.results[-1]


def create_agent_run(
    task: str,
    include_debugger: bool = False,
) -> AgentRun:
    route = route_task(task)

    pipeline = build_pipeline(
        task,
        include_debugger=include_debugger,
    )

    return AgentRun(
        task=task,
        route=route,
        pipeline=pipeline,
        results=[],
    )


# ============================================================
# DISPLAY
# ============================================================

def print_intelligence_status(
    task: str,
    run: AgentRun,
):
    print()
    print("=" * 60)
    print("SYNORA AGENT INTELLIGENCE V2")
    print("=" * 60)

    print()
    print(f"Task: {task}")

    print()
    print("Routing:")
    print(f"  Role: {run.route.role}")
    print(f"  Confidence: {run.route.confidence:.3f}")
    print(f"  Reason: {run.route.reason}")

    print()
    print("Pipeline:")

    for item in describe_pipeline(
        run.pipeline
    ):
        print(f"  {item}")

    print()
    print("=" * 60)


# ============================================================
# TEST HELPERS
# ============================================================

def run_self_test():
    print("=" * 60)
    print("SYNORA AGENT INTELLIGENCE V2 TEST")
    print("=" * 60)

    tests = [
        (
            "buat endpoint RPC baru",
            "coder",
        ),
        (
            "debug error cargo check",
            "debugger",
        ),
        (
            "review keamanan authentication",
            "reviewer",
        ),
        (
            "buat test untuk RPC",
            "tester",
        ),
        (
            "buat rencana perubahan RPC",
            "planner",
        ),
    ]

    for task, expected in tests:
        decision = route_task(task)

        print()
        print(f"Task: {task}")
        print(f"Role: {decision.role}")
        print(
            f"Confidence: {decision.confidence:.3f}"
        )

        if decision.role != expected:
            raise AssertionError(
                f"Routing gagal: "
                f"{task!r} -> "
                f"{decision.role}, "
                f"expected {expected}"
            )

    run = create_agent_run(
        "buat endpoint RPC baru"
    )

    if run.pipeline != list(DEFAULT_PIPELINE):
        raise AssertionError(
            "Default pipeline tidak valid."
        )

    debug_run = create_agent_run(
        "debug cargo test gagal"
    )

    if "debugger" not in debug_run.pipeline:
        raise AssertionError(
            "Debug pipeline tidak memiliki debugger."
        )

    prompt = build_role_prompt(
        "planner",
        "buat endpoint RPC baru",
        "fn example() {}",
    )

    if "ROLE" not in prompt:
        raise AssertionError(
            "Role prompt tidak terbentuk."
        )

    if "SOURCE CONTEXT" not in prompt:
        raise AssertionError(
            "Source context tidak masuk prompt."
        )

    print()
    print("PASS: routing.")
    print("PASS: pipeline.")
    print("PASS: role prompt.")
    print("PASS: debugger pipeline.")
    print()
    print("=" * 60)
    print("✓ INTELLIGENCE V2 TEST PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run_self_test()
