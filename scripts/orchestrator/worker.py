#!/usr/bin/env python3
"""
Worker flow framework — structured, checkpointed Claude Code worker stages.

Each intellectual task (normalize, discuss, write, review, revise) is a worker
step with explicit inputs/outputs. This module defines the data structures and
dispatches to the correct handler.

Stages discuss/write/review/revise invoke Claude Code via claude_runner.
The normalize stage runs locally (no LLM needed).
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.orchestrator.claude_runner import (
    build_data_context,
    build_memory_context,
    build_watchlist_context,
    load_prompt_template,
    run_claude,
)

WORKSPACE = Path(__file__).resolve().parents[2]

# Budget limits per stage (USD) — prevent runaway costs
# discuss is now 6 Claude calls: 4 roles × $1.0 + challenge $1.0 + editor $1.5
STAGE_BUDGETS = {
    "discuss": 6.5,  # 4×1.0 + 1.0 + 1.5 (managed per-call inside _discuss)
    "write": 5.0,
    "review": 2.0,
    "revise": 4.0,
}

# Timeout per stage (seconds)
# discuss: 6 calls × 180s each = 1080s max, but most finish faster
STAGE_TIMEOUTS = {
    "discuss": 1200,
    "write": 600,
    "review": 180,
    "revise": 480,
}


@dataclass
class WorkerStep:
    """A single step in a worker flow."""
    name: str
    inputs: list[str]
    output_key: str
    max_retries: int = 1
    prompt_template: str = ""  # path relative to scripts/prompts/


@dataclass
class WorkerFlow:
    """Ordered sequence of worker steps for a task type."""
    task_type: str
    steps: list[WorkerStep] = field(default_factory=list)


@dataclass
class StepResult:
    """Result of executing a single worker step."""
    step_name: str
    status: str  # "ok" | "error" | "skipped"
    output_path: str = ""
    error: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "status": self.status,
            "output_path": self.output_path,
            "error": self.error,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Flow definitions
# ---------------------------------------------------------------------------

FLOWS: dict[str, WorkerFlow] = {
    "premarket": WorkerFlow(
        task_type="premarket",
        steps=[
            WorkerStep("normalize", ["manifest"], "normalized_data"),
            WorkerStep("write", ["normalized_data", "memory_context"], "draft"),
            WorkerStep("review", ["draft", "quality_rules"], "review_feedback"),
            WorkerStep("revise", ["draft", "review_feedback"], "final"),
        ],
    ),
    "closing": WorkerFlow(
        task_type="closing",
        steps=[
            WorkerStep("normalize", ["manifest", "premarket_report"], "normalized_data"),
            WorkerStep("discuss", ["normalized_data"], "discussion"),
            WorkerStep("write", ["normalized_data", "discussion", "template"], "draft"),
            WorkerStep("review", ["draft", "quality_rules"], "review_feedback"),
            WorkerStep("revise", ["draft", "review_feedback"], "final"),
        ],
    ),
    "weekly": WorkerFlow(
        task_type="weekly",
        steps=[
            WorkerStep("normalize", ["manifest"], "normalized_data"),
            WorkerStep("discuss", ["normalized_data"], "discussion"),
            WorkerStep("write", ["normalized_data", "discussion", "template"], "draft"),
            WorkerStep("review", ["draft", "quality_rules"], "review_feedback"),
            WorkerStep("revise", ["draft", "review_feedback"], "final"),
        ],
    ),
    "philosophy": WorkerFlow(
        task_type="philosophy",
        steps=[
            WorkerStep("normalize", ["manifest"], "normalized_data"),
            WorkerStep("write", ["normalized_data", "memory_context"], "draft"),
            WorkerStep("review", ["draft"], "review_feedback"),
            WorkerStep("revise", ["draft", "review_feedback"], "final"),
        ],
    ),
}

# ---------------------------------------------------------------------------
# Worker stage dispatch
# ---------------------------------------------------------------------------

def flow_stages(task_type: str) -> list[str]:
    """Return the ordered stage names declared for a task type's flow.

    Returns an empty list if the task type has no registered flow.
    """
    flow = FLOWS.get(task_type)
    if flow is None:
        return []
    return [step.name for step in flow.steps]


def run_worker(task_type: str, date: str, stage: str,
               checkpoints: dict) -> dict:
    """Execute a single worker stage for the given task.

    Args:
        task_type: One of the registered task types.
        date: Task date (YYYY-MM-DD).
        stage: Worker stage name (normalize, discuss, write, review, revise).
        checkpoints: Dict of already-completed stage checkpoints from TaskState.

    Returns:
        Dict with at least 'output_path' key.

    Raises:
        ValueError: If stage has no handler or is not part of the task's flow.
    """
    handler = _STAGE_HANDLERS.get(stage)
    if handler is None:
        raise ValueError(f"No handler for worker stage '{stage}'")

    # Validate stage belongs to this task type's declared flow
    valid_stages = flow_stages(task_type)
    if valid_stages and stage not in valid_stages:
        raise ValueError(
            f"Stage '{stage}' is not part of the '{task_type}' flow. "
            f"Valid worker stages: {' → '.join(valid_stages)}"
        )

    print(f"  [worker] Running {stage} for {task_type}/{date}")
    result = handler(task_type, date, checkpoints)
    print(f"  [worker] {stage} → {result.status}")
    return result.to_dict()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _output_dir(task_type: str, date: str) -> Path:
    d = WORKSPACE / "data" / "worker" / task_type / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_normalized(checkpoints: dict) -> dict | None:
    """Load normalized.json from checkpoints."""
    norm_path = checkpoints.get("normalize", {}).get("output_path", "")
    if not norm_path or not Path(norm_path).exists():
        return None
    try:
        with open(norm_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _discussion_roles(task_type: str) -> list[str]:
    if task_type == "weekly":
        return [
            "module_macro", "module_structure", "module_sectors",
            "module_watchlist", "module_risk", "cross_challenge", "editor_ruling",
        ]
    return ["bull", "bear", "quant", "risk_officer", "editor"]


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


# ---------------------------------------------------------------------------
# Stage handlers
# ---------------------------------------------------------------------------

def _normalize(task_type: str, date: str, checkpoints: dict) -> StepResult:
    """Read raw collection outputs and structure them for analysis.

    This stage runs locally — no LLM call needed.
    """
    manifest_path = checkpoints.get("collect", {}).get("manifest_path", "")
    if not manifest_path:
        return StepResult(
            step_name="normalize",
            status="error",
            error="No collection manifest path in checkpoints — run collect stage first",
            timestamp=_now_iso(),
        )

    mp = Path(manifest_path)
    if not mp.exists():
        return StepResult(
            step_name="normalize",
            status="error",
            error=f"Collection manifest file not found: {manifest_path}",
            timestamp=_now_iso(),
        )

    try:
        with open(mp, encoding="utf-8") as f:
            collection_manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return StepResult(
            step_name="normalize",
            status="error",
            error=f"Cannot read collection manifest: {exc}",
            timestamp=_now_iso(),
        )

    artifacts: dict[str, dict] = {}
    for script in collection_manifest.get("scripts", []):
        name = script.get("name", "")
        output_file = script.get("output_file", "")
        artifacts[name] = {
            "status": script.get("status", "unknown"),
            "output_file": output_file,
            "output_lines": script.get("output_lines", 0),
            "available": bool(output_file and Path(output_file).exists()),
        }

    out = _output_dir(task_type, date)
    output_path = out / "normalized.json"

    normalized = {
        "task_type": task_type,
        "date": date,
        "source_manifest": manifest_path,
        "artifacts": artifacts,
        "sections": {},
        "generated_at": _now_iso(),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)

    return StepResult(
        step_name="normalize",
        status="ok",
        output_path=str(output_path),
        timestamp=_now_iso(),
    )


def _discuss(task_type: str, date: str, checkpoints: dict) -> StepResult:
    """Multi-round agent team debate via Claude Code.

    Architecture (3 rounds, each a separate Claude call):
      Round 1: 4 independent roles analyze the data (bull/bear/quant/risk)
               Each role gets the same data but a different system prompt.
               They cannot see each other's output → genuine independence.
      Round 2: Cross-challenge — one Claude sees all 4 outputs and finds
               contradictions, blind spots, and logic holes.
      Round 3: Editor ruling — one Claude sees everything and makes the
               final call with overturn conditions.

    On 2-core 4GB server: all rounds run serially (no parallel Claude calls).
    """
    out = _output_dir(task_type, date)
    output_path = out / "discussion.json"

    # Load normalized data for context
    normalized = _load_normalized(checkpoints)
    if normalized is None:
        return StepResult(
            step_name="discuss", status="error",
            error="Normalized data not available — run normalize stage first",
            timestamp=_now_iso(),
        )

    data_context = build_data_context(normalized.get("artifacts", {}), date)
    memory_context = build_memory_context(date)
    watchlist_context = build_watchlist_context()

    base_prompt = (
        f"任务类型: {task_type}\n日期: {date}\n\n"
        f"{watchlist_context}\n\n"
        f"{memory_context}\n\n"
        f"{data_context}\n\n"
    )

    prompts_dir = Path(__file__).resolve().parent / "prompts" / "roles"

    # ── Round 1: Independent role analysis ──────────────────────────
    round1_roles = ["bull", "bear", "quant", "risk"]
    round1_results = {}
    round1_budget = 1.0  # per role

    for role in round1_roles:
        role_prompt_path = prompts_dir / f"{role}.md"
        if not role_prompt_path.exists():
            round1_results[role] = {"error": f"Prompt not found: {role}.md"}
            continue

        system_prompt = role_prompt_path.read_text(encoding="utf-8")
        prompt = base_prompt + f"请按照你的角色设定分析以上数据，输出纯 JSON。"

        print(f"  [discuss] Round 1 — {role}...")
        result = run_claude(
            prompt,
            system_prompt=system_prompt,
            timeout_seconds=180,
            max_budget_usd=round1_budget,
            allowed_tools=[],
        )

        if result.success:
            try:
                round1_results[role] = json.loads(result.output)
            except json.JSONDecodeError:
                round1_results[role] = {"raw_output": result.output[:5000]}
        else:
            round1_results[role] = {"error": result.error[:500]}
            print(f"  [discuss] Round 1 — {role} FAILED: {result.error[:200]}", file=sys.stderr)

    # ── Round 2: Cross-challenge ────────────────────────────────────
    print(f"  [discuss] Round 2 — cross-challenge...")
    challenge_prompt_path = prompts_dir / "cross_challenge.md"
    if challenge_prompt_path.exists():
        challenge_system = challenge_prompt_path.read_text(encoding="utf-8")
    else:
        challenge_system = "你是交叉质疑主持人。找出 4 个角色分析中的矛盾和盲点。输出 JSON。"

    round1_text = json.dumps(round1_results, indent=2, ensure_ascii=False)
    challenge_prompt = (
        f"以下是 4 个独立角色对 {date} 市场数据的分析：\n\n"
        f"```json\n{round1_text}\n```\n\n"
        f"请找出他们之间的矛盾、盲点和逻辑漏洞。输出纯 JSON。"
    )

    challenge_result = run_claude(
        challenge_prompt,
        system_prompt=challenge_system,
        timeout_seconds=180,
        max_budget_usd=1.0,
        allowed_tools=[],
    )

    if challenge_result.success:
        try:
            round2_output = json.loads(challenge_result.output)
        except json.JSONDecodeError:
            round2_output = {"raw_output": challenge_result.output[:5000]}
    else:
        round2_output = {"error": challenge_result.error[:500]}

    # ── Round 3: Editor ruling ──────────────────────────────────────
    print(f"  [discuss] Round 3 — editor ruling...")
    editor_prompt_path = prompts_dir / "editor.md"
    if editor_prompt_path.exists():
        editor_system = editor_prompt_path.read_text(encoding="utf-8")
    else:
        editor_system = "你是主编。综合所有角色分析和质疑，做最终裁决。输出 JSON。"

    round2_text = json.dumps(round2_output, indent=2, ensure_ascii=False)
    editor_prompt = (
        f"## 4 个角色的独立分析\n\n```json\n{round1_text}\n```\n\n"
        f"## 交叉质疑报告\n\n```json\n{round2_text}\n```\n\n"
        f"请综合以上所有信息，做出最终裁决。输出纯 JSON。"
    )

    editor_result = run_claude(
        editor_prompt,
        system_prompt=editor_system,
        timeout_seconds=180,
        max_budget_usd=1.5,
        allowed_tools=[],
    )

    if editor_result.success:
        try:
            round3_output = json.loads(editor_result.output)
        except json.JSONDecodeError:
            round3_output = {"raw_output": editor_result.output[:5000]}
    else:
        round3_output = {"error": editor_result.error[:500]}

    # ── Assemble final discussion document ──────────────────────────
    discussion = {
        "task_type": task_type,
        "date": date,
        "architecture": "3-round agent team debate",
        "round_1_independent": round1_results,
        "round_2_cross_challenge": round2_output,
        "round_3_editor_ruling": round3_output,
        "generated_at": _now_iso(),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(discussion, f, indent=2, ensure_ascii=False)

    # Count successes
    r1_ok = sum(1 for r in round1_results.values() if "error" not in r)
    print(f"  [discuss] Done: R1={r1_ok}/4 roles, R2={'ok' if 'error' not in round2_output else 'fail'}, R3={'ok' if 'error' not in round3_output else 'fail'}")

    return StepResult(
        step_name="discuss",
        status="ok",
        output_path=str(output_path),
        timestamp=_now_iso(),
    )


def _write(task_type: str, date: str, checkpoints: dict) -> StepResult:
    """Draft generation via Claude Code.

    Takes normalized data + discussion output and produces a markdown draft.
    """
    out = _output_dir(task_type, date)
    output_path = out / "draft.md"

    # Load normalized data
    normalized = _load_normalized(checkpoints)
    if normalized is None:
        return StepResult(
            step_name="write", status="error",
            error="Normalized data not available",
            timestamp=_now_iso(),
        )

    data_context = build_data_context(normalized.get("artifacts", {}), date)
    memory_context = build_memory_context(date)

    # Load discussion if available
    discuss_path = checkpoints.get("discuss", {}).get("output_path", "")
    discussion_text = ""
    if discuss_path and Path(discuss_path).exists():
        try:
            discussion_text = Path(discuss_path).read_text(encoding="utf-8")
            if len(discussion_text) > 20000:
                discussion_text = discussion_text[:20000] + "\n... (截断)"
        except OSError:
            discussion_text = "（讨论纪要读取失败）"

    # Load system prompt
    try:
        system_prompt = load_prompt_template("write_report")
    except FileNotFoundError as e:
        return StepResult(
            step_name="write", status="error",
            error=str(e), timestamp=_now_iso(),
        )

    # Load premarket report for closing tasks
    premarket_ref = ""
    if task_type == "closing":
        pm_path = WORKSPACE / "reports" / "daily" / f"{date}-pre-market.md"
        if pm_path.exists():
            try:
                pm_text = pm_path.read_text(encoding="utf-8")
                if len(pm_text) > 15000:
                    pm_text = pm_text[:15000] + "\n... (截断)"
                premarket_ref = f"\n# 今日盘前报告（供对照验证/证伪）\n\n{pm_text}\n"
            except OSError:
                pass

    # Build watchlist context so Claude knows which stocks/ETFs to cover
    watchlist_context = build_watchlist_context()

    prompt_parts = [
        f"任务类型: {task_type}",
        f"日期: {date}",
        "",
        watchlist_context,
        "",
        memory_context,
        "",
        data_context,
    ]
    if discussion_text:
        prompt_parts.extend(["", "# 讨论纪要\n", discussion_text])
    if premarket_ref:
        prompt_parts.append(premarket_ref)
    prompt_parts.extend([
        "",
        f"请按照系统提示中 {task_type} 的报告结构要求，撰写完整的 Markdown 报告。",
    ])

    prompt = "\n".join(prompt_parts)

    result = run_claude(
        prompt,
        system_prompt=system_prompt,
        timeout_seconds=STAGE_TIMEOUTS["write"],
        max_budget_usd=STAGE_BUDGETS["write"],
        allowed_tools=[],  # all data passed via prompt — no tools needed
    )

    if not result.success:
        return StepResult(
            step_name="write", status="error",
            error=f"Claude Code failed: {result.error}",
            timestamp=_now_iso(),
        )

    output_path.write_text(result.output, encoding="utf-8")

    return StepResult(
        step_name="write",
        status="ok",
        output_path=str(output_path),
        timestamp=_now_iso(),
    )


def _review(task_type: str, date: str, checkpoints: dict) -> StepResult:
    """Quality review via Claude Code.

    Checks the draft against structural and content quality rules.
    Returns structured review feedback for the revise stage.
    """
    out = _output_dir(task_type, date)
    output_path = out / "review.json"

    # Load the draft
    write_path = checkpoints.get("write", {}).get("output_path", "")
    if not write_path or not Path(write_path).exists():
        return StepResult(
            step_name="review", status="error",
            error="Draft not available — run write stage first",
            timestamp=_now_iso(),
        )

    draft = Path(write_path).read_text(encoding="utf-8")

    # Load system prompt
    try:
        system_prompt = load_prompt_template("review_report")
    except FileNotFoundError as e:
        return StepResult(
            step_name="review", status="error",
            error=str(e), timestamp=_now_iso(),
        )

    prompt = (
        f"任务类型: {task_type}\n日期: {date}\n\n"
        f"# 待审核报告\n\n{draft}\n\n"
        f"请按照系统提示中的审核维度和输出格式，对这份报告进行严格质检。"
    )

    result = run_claude(
        prompt,
        system_prompt=system_prompt,
        timeout_seconds=STAGE_TIMEOUTS["review"],
        max_budget_usd=STAGE_BUDGETS["review"],
        allowed_tools=[],
    )

    if not result.success:
        return StepResult(
            step_name="review", status="error",
            error=f"Claude Code failed: {result.error}",
            timestamp=_now_iso(),
        )

    # Parse review output
    try:
        review = json.loads(result.output)
    except json.JSONDecodeError:
        review = {
            "task_type": task_type,
            "date": date,
            "passed": False,
            "verdict": "REVISE",
            "raw_output": result.output,
            "parse_error": "Review output was not valid JSON",
            "generated_at": _now_iso(),
        }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=2, ensure_ascii=False)

    return StepResult(
        step_name="review",
        status="ok",
        output_path=str(output_path),
        timestamp=_now_iso(),
    )


def _revise(task_type: str, date: str, checkpoints: dict) -> StepResult:
    """Revision stage via Claude Code.

    If review passed (verdict=PASS), promotes draft to final without LLM call.
    If review found issues, Claude Code revises based on feedback.
    """
    out = _output_dir(task_type, date)
    final_path = out / "final.md"

    write_path = checkpoints.get("write", {}).get("output_path", "")
    review_path = checkpoints.get("review", {}).get("output_path", "")

    if not write_path or not Path(write_path).exists():
        return StepResult(
            step_name="revise", status="error",
            error="Draft not available for revision",
            timestamp=_now_iso(),
        )

    draft = Path(write_path).read_text(encoding="utf-8")

    # Check if review passed — if so, skip LLM call
    review_data = {}
    if review_path and Path(review_path).exists():
        try:
            with open(review_path, encoding="utf-8") as f:
                review_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    verdict = review_data.get("verdict", "REVISE")
    if verdict == "PASS":
        print("  [worker] Review passed — promoting draft to final (no revision needed)")
        final_path.write_text(draft, encoding="utf-8")
        return StepResult(
            step_name="revise",
            status="ok",
            output_path=str(final_path),
            timestamp=_now_iso(),
        )

    # Review has issues — invoke Claude Code to revise
    try:
        system_prompt = load_prompt_template("revise_report")
    except FileNotFoundError as e:
        # Fallback: just promote draft
        final_path.write_text(draft, encoding="utf-8")
        return StepResult(
            step_name="revise", status="ok",
            output_path=str(final_path),
            error=f"Revise template not found ({e}), promoted draft as-is",
            timestamp=_now_iso(),
        )

    review_text = json.dumps(review_data, indent=2, ensure_ascii=False)

    prompt = (
        f"任务类型: {task_type}\n日期: {date}\n\n"
        f"# 审核反馈\n```json\n{review_text}\n```\n\n"
        f"# 原始报告草稿\n\n{draft}\n\n"
        f"请根据审核反馈修订报告，输出完整的修订版 Markdown。"
    )

    result = run_claude(
        prompt,
        system_prompt=system_prompt,
        timeout_seconds=STAGE_TIMEOUTS["revise"],
        max_budget_usd=STAGE_BUDGETS["revise"],
        allowed_tools=[],  # draft + review feedback in prompt — no tools needed
    )

    if not result.success:
        # Fallback: promote unrevised draft
        print(f"  [worker] Revision failed ({result.error}), promoting draft as-is",
              file=sys.stderr)
        final_path.write_text(draft, encoding="utf-8")
        return StepResult(
            step_name="revise", status="ok",
            output_path=str(final_path),
            error=f"Revision failed, promoted unrevised draft: {result.error}",
            timestamp=_now_iso(),
        )

    final_path.write_text(result.output, encoding="utf-8")

    return StepResult(
        step_name="revise",
        status="ok",
        output_path=str(final_path),
        timestamp=_now_iso(),
    )


_STAGE_HANDLERS = {
    "normalize": _normalize,
    "discuss": _discuss,
    "write": _write,
    "review": _review,
    "revise": _revise,
}
