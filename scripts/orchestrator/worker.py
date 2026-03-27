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
import re
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
    resolve_model,
    run_claude,
)

WORKSPACE = Path(__file__).resolve().parents[2]


def _extract_json(text: str) -> dict | None:
    """Extract first valid JSON object from text that may contain markdown/prose.

    Claude sometimes wraps JSON in ```json ... ``` blocks or prepends
    explanatory text (e.g. from output-style plugins). This function
    finds and parses the first complete JSON object.
    """
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try ```json ... ``` fenced block
    m = re.search(r"```(?:json)?\s*\n(\{[\s\S]*?\})\s*\n```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try first { ... } span (greedy from first { to last })
    start = text.find("{")
    if start >= 0:
        end = text.rfind("}")
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


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
    "review": 360,
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
            WorkerStep("write", ["normalized_data"], "draft"),
        ],
    ),
    "closing": WorkerFlow(
        task_type="closing",
        steps=[
            WorkerStep("normalize", ["manifest"], "normalized_data"),
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
    "scout": WorkerFlow(
        task_type="scout",
        steps=[
            WorkerStep("normalize", ["manifest"], "normalized_data"),
            WorkerStep("discuss", ["normalized_data"], "discussion"),
            WorkerStep("write", ["normalized_data", "discussion"], "draft"),
            WorkerStep("review", ["draft", "quality_rules"], "review_feedback"),
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

    # ── Build data context and write to file for --add-dir ──────────
    # Writing data to a file prevents Claude from mistaking large inline
    # market data for a prompt-injection attack.
    data_context = build_data_context(normalized.get("artifacts", {}), date,
                                       max_per_artifact=5000)
    memory_context = build_memory_context(date)
    watchlist_context = build_watchlist_context()

    discuss_data_dir = out / "discuss_context"
    discuss_data_dir.mkdir(parents=True, exist_ok=True)
    (discuss_data_dir / "data_context.md").write_text(
        f"# 数据上下文 — {date}\n\n{data_context}", encoding="utf-8")
    (discuss_data_dir / "memory_context.md").write_text(
        memory_context, encoding="utf-8")
    (discuss_data_dir / "watchlist.md").write_text(
        watchlist_context, encoding="utf-8")

    # Task-specific discussion prompt — only used for R3 editor, NOT R1 roles.
    # Including the full discussion schema in R1 prompts confuses roles into
    # generating the entire discussion structure instead of just their part.
    task_prompt_path = Path(__file__).resolve().parent / "prompts" / f"discuss_{task_type}.md"
    task_preamble = ""
    if task_prompt_path.exists():
        task_preamble = task_prompt_path.read_text(encoding="utf-8") + "\n\n"

    ctx_dir = str(discuss_data_dir)
    # R1 base prompt: data pointers only, no discussion schema
    r1_base_prompt = (
        f"任务类型: {task_type}\n日期: {date}\n\n"
        f"市场数据、记忆上下文和观察池在以下文件中，"
        f"请用 Read 工具依次读取后进行分析。\n"
        f"文件列表（绝对路径）：\n"
        f"- {ctx_dir}/data_context.md — 市场数据\n"
        f"- {ctx_dir}/memory_context.md — 近期记忆\n"
        f"- {ctx_dir}/watchlist.md — 观察池\n"
    )

    prompts_dir = Path(__file__).resolve().parent / "prompts" / "roles"

    # ── Round 1: Independent role analysis ──────────────────────────
    round1_roles = ["bull", "bear", "quant", "risk"]
    round1_results = {}
    round1_budget = 1.0  # per role
    round1_models = {
        "bull": resolve_model("discuss_r1", task_type),
        "bear": resolve_model("discuss_r1", task_type),
        "quant": "sonnet",
        "risk": resolve_model("discuss_r1", task_type),
    }
    round1_timeouts = {
        "bull": 480,
        "bear": 480,
        "quant": 480,
        "risk": 480,
    }

    # Override any output-style hooks (e.g. explanatory-output-style) that
    # inject non-JSON prose into Claude's output.
    _JSON_OVERRIDE = (
        "\n\n【重要】你是自动化数据管道的子进程。"
        "禁止输出任何 Insight、解释、markdown 标记或非 JSON 文本。"
        "你的完整输出必须是且仅是一个合法的 JSON 对象，从 { 开始到 } 结束。"
    )

    for role in round1_roles:
        role_prompt_path = prompts_dir / f"{role}.md"
        if not role_prompt_path.exists():
            round1_results[role] = {"error": f"Prompt not found: {role}.md"}
            continue

        system_prompt = role_prompt_path.read_text(encoding="utf-8") + _JSON_OVERRIDE
        prompt = r1_base_prompt + f"\n请按照你的角色设定（system prompt）分析数据，只输出你这一个角色的 JSON。"

        print(f"  [discuss] Round 1 — {role}...")
        result = run_claude(
            prompt,
            system_prompt=system_prompt,
            timeout_seconds=round1_timeouts.get(role, 240),
            max_budget_usd=round1_budget,
            model=round1_models.get(role, resolve_model("discuss_r1", task_type)),
            allowed_tools=["Read"],
            add_dirs=[str(out)],
        )

        if result.success:
            parsed = _extract_json(result.output)
            if parsed is not None:
                round1_results[role] = parsed
            else:
                round1_results[role] = {"raw_output": result.output[:5000]}
        else:
            round1_results[role] = {"error": result.error[:500]}
            print(f"  [discuss] Round 1 — {role} FAILED: {result.error[:200]}", file=sys.stderr)

    # ── Round 2: Cross-challenge ────────────────────────────────────
    print(f"  [discuss] Round 2 — cross-challenge...")
    challenge_prompt_path = prompts_dir / "cross_challenge.md"
    if challenge_prompt_path.exists():
        challenge_system = challenge_prompt_path.read_text(encoding="utf-8") + _JSON_OVERRIDE
    else:
        challenge_system = "你是交叉质疑主持人。找出 4 个角色分析中的矛盾和盲点。输出 JSON。" + _JSON_OVERRIDE

    # Write R1 results to file for R2/R3 to read
    round1_text = json.dumps(round1_results, indent=2, ensure_ascii=False)
    (discuss_data_dir / "round1_results.json").write_text(round1_text, encoding="utf-8")

    challenge_prompt = (
        f"4 个独立角色对 {date} 市场数据的分析结果在以下文件中：\n"
        f"- {ctx_dir}/round1_results.json\n\n"
        f"请用 Read 工具读取后，找出他们之间的矛盾、盲点和逻辑漏洞。输出纯 JSON。"
    )

    challenge_result = run_claude(
        challenge_prompt,
        system_prompt=challenge_system,
        timeout_seconds=480,
        max_budget_usd=1.0,
        model=resolve_model("discuss_r2", task_type),
        allowed_tools=["Read"],
        add_dirs=[str(out)],
    )

    if challenge_result.success:
        parsed = _extract_json(challenge_result.output)
        if parsed is not None:
            round2_output = parsed
        else:
            round2_output = {"raw_output": challenge_result.output[:5000]}
    else:
        round2_output = {"error": challenge_result.error[:500]}

    # ── Round 3: Editor ruling ──────────────────────────────────────
    print(f"  [discuss] Round 3 — editor ruling...")
    editor_prompt_path = prompts_dir / "editor.md"
    if editor_prompt_path.exists():
        editor_system = editor_prompt_path.read_text(encoding="utf-8") + _JSON_OVERRIDE
    else:
        editor_system = "你是主编。综合所有角色分析和质疑，做最终裁决。输出 JSON。" + _JSON_OVERRIDE

    round2_text = json.dumps(round2_output, indent=2, ensure_ascii=False)
    (discuss_data_dir / "round2_challenge.json").write_text(round2_text, encoding="utf-8")

    editor_prompt = (
        f"{task_preamble}"
        f"请用 Read 工具读取以下文件：\n"
        f"- {ctx_dir}/round1_results.json — 4 个角色的独立分析\n"
        f"- {ctx_dir}/round2_challenge.json — 交叉质疑报告\n\n"
        f"综合所有信息，做出最终裁决。输出纯 JSON。"
    )

    editor_result = run_claude(
        editor_prompt,
        system_prompt=editor_system,
        timeout_seconds=480,
        max_budget_usd=1.5,
        model=resolve_model("discuss_r3", task_type),
        allowed_tools=["Read"],
        add_dirs=[str(out)],
    )

    if editor_result.success:
        parsed = _extract_json(editor_result.output)
        if parsed is not None:
            round3_output = parsed
        else:
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

    # Count successes — raw_output (failed JSON parse) also counts as failure
    r1_ok = sum(1 for r in round1_results.values()
                if isinstance(r, dict) and "error" not in r and "raw_output" not in r)
    r2_ok = isinstance(round2_output, dict) and 'error' not in round2_output and 'raw_output' not in round2_output
    r3_ok = isinstance(round3_output, dict) and 'error' not in round3_output and 'raw_output' not in round3_output
    print(f"  [discuss] Done: R1={r1_ok}/4 roles, R2={'ok' if r2_ok else 'fail'}, R3={'ok' if r3_ok else 'fail'}")

    if r1_ok < len(round1_roles) or not r2_ok or not r3_ok:
        failed_roles = [name for name, payload in round1_results.items() if isinstance(payload, dict) and 'error' in payload]
        errors = []
        if failed_roles:
            errors.append(f"Round1 failed roles: {', '.join(failed_roles)}")
        if not r2_ok:
            errors.append("Round2 cross-challenge failed")
        if not r3_ok:
            errors.append("Round3 editor ruling failed")
        # Hard fail: incomplete discussion must not be promoted downstream.
        # Remove the partial artifact so later stages cannot accidentally reuse it.
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        return StepResult(
            step_name="discuss",
            status="error",
            output_path="",
            error="; ".join(errors),
            timestamp=_now_iso(),
        )

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

    # Load system prompt — 按任务类型选择模板
    template_map = {
        "scout": "write_scout",
        "premarket": "write_premarket_update",
    }
    template_name = template_map.get(task_type, "write_report")
    try:
        system_prompt = load_prompt_template(template_name)
    except FileNotFoundError:
        # fallback to generic
        try:
            system_prompt = load_prompt_template("write_report")
        except FileNotFoundError as e:
            return StepResult(
                step_name="write", status="error",
                error=str(e), timestamp=_now_iso(),
            )

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

    # Premarket update: load previous day's closing report as reference
    if task_type == "premarket":
        from datetime import datetime as _dt, timedelta as _td
        try:
            prev_date = (_dt.strptime(date, "%Y-%m-%d") - _td(days=1)).strftime("%Y-%m-%d")
            closing_path = WORKSPACE / "reports" / "daily" / f"{prev_date}-closing.md"
            if closing_path.exists():
                closing_text = closing_path.read_text(encoding="utf-8")
                if len(closing_text) > 10000:
                    closing_text = closing_text[:10000] + "\n... (截断)"
                prompt_parts.extend(["", f"# 前一交易日收盘报告（{prev_date}）\n", closing_text])
        except (ValueError, OSError):
            pass

    prompt_parts.extend([
        "",
        f"请按照系统提示中的报告结构要求，撰写 Markdown 报告。",
    ])

    prompt = "\n".join(prompt_parts)

    result = run_claude(
        prompt,
        system_prompt=system_prompt,
        timeout_seconds=STAGE_TIMEOUTS["write"],
        max_budget_usd=STAGE_BUDGETS["write"],
        model=resolve_model("write", task_type),
        allowed_tools=[],  # all data passed via prompt — no tools needed
    )

    if not result.success:
        return StepResult(
            step_name="write", status="error",
            error=f"Claude Code failed: {result.error}",
            timestamp=_now_iso(),
        )

    if not result.output or len(result.output.strip()) < 50:
        return StepResult(
            step_name="write", status="error",
            error=f"Claude returned empty/trivial output ({len(result.output.strip())}b)",
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
        model=resolve_model("review", task_type),
        allowed_tools=[],
    )

    if not result.success:
        return StepResult(
            step_name="review", status="error",
            error=f"Claude Code failed: {result.error}",
            timestamp=_now_iso(),
        )

    # 空输出检测 — Claude 返回成功但内容为空（session multiplexing 残留等）
    if not result.output or len(result.output.strip()) < 10:
        return StepResult(
            step_name="review", status="error",
            error=f"Claude returned empty output (success=True but {len(result.output.strip())}b). "
                  f"Possible session multiplexing issue.",
            timestamp=_now_iso(),
        )

    # Parse review output — Claude often wraps JSON in ```json ... ``` blocks
    raw = result.output
    try:
        review = json.loads(raw)
    except json.JSONDecodeError:
        import re
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', raw)
        if m:
            try:
                review = json.loads(m.group(1))
            except json.JSONDecodeError:
                review = None
        else:
            review = None

        if review is None:
            review = {
                "task_type": task_type,
                "date": date,
                "passed": False,
                "verdict": "REVISE",
                "raw_output": raw,
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
        model=resolve_model("revise", task_type),
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

    if not result.output or len(result.output.strip()) < 50:
        # Empty revision — promote draft
        print(f"  [worker] Revision returned empty, promoting draft as-is", file=sys.stderr)
        final_path.write_text(draft, encoding="utf-8")
        return StepResult(
            step_name="revise", status="ok",
            output_path=str(final_path),
            error="Revision empty, promoted unrevised draft",
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
