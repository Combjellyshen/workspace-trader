#!/usr/bin/env python3
"""
Worker flow framework — structured, checkpointed Claude Code worker stages.

Each intellectual task (normalize, discuss, write, review, revise) is a worker
step with explicit inputs/outputs. This module defines the data structures and
dispatches to the correct handler.

Current implementation: scaffolded with clear function signatures. Real Claude
Code subagent integration will be added in Phase 2.
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parents[2]


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
# Flow definitions (will grow as prompt templates are added)
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
}

# ---------------------------------------------------------------------------
# Worker stage dispatch
# ---------------------------------------------------------------------------

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
    """
    handler = _STAGE_HANDLERS.get(stage)
    if handler is None:
        raise ValueError(f"No handler for worker stage '{stage}'")

    print(f"  [worker] Running {stage} for {task_type}/{date}")
    result = handler(task_type, date, checkpoints)
    print(f"  [worker] {stage} → {result.status}")
    return result.to_dict()


# ---------------------------------------------------------------------------
# Stage handlers (scaffolded — real logic added in Phase 2)
# ---------------------------------------------------------------------------

def _normalize(task_type: str, date: str, checkpoints: dict) -> StepResult:
    """Read raw collection outputs and structure them for analysis.

    Reads the collection manifest, loads each script's output,
    and produces a unified normalized JSON for downstream stages.
    """
    manifest_path = checkpoints.get("collect", {}).get("manifest_path", "")
    if not manifest_path or not Path(manifest_path).exists():
        return StepResult(
            step_name="normalize",
            status="error",
            error="No collection manifest found — run collect stage first",
            timestamp=_now_iso(),
        )

    output_dir = WORKSPACE / "data" / "worker" / task_type / date
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "normalized.json"

    # Scaffold: write a stub normalized file pointing to the manifest
    normalized = {
        "task_type": task_type,
        "date": date,
        "source_manifest": manifest_path,
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
    """Multi-POV discussion stage.

    For daily tasks: 5-role debate (bull/bear/quant/risk/editor).
    For weekly: 5-module research → cross-challenge → editor ruling.
    """
    output_dir = WORKSPACE / "data" / "worker" / task_type / date
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "discussion.json"

    discussion = {
        "task_type": task_type,
        "date": date,
        "roles": _discussion_roles(task_type),
        "arguments": {},  # populated by Claude Code subagents in Phase 2
        "ruling": None,
        "generated_at": _now_iso(),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(discussion, f, indent=2, ensure_ascii=False)

    return StepResult(
        step_name="discuss",
        status="ok",
        output_path=str(output_path),
        timestamp=_now_iso(),
    )


def _write(task_type: str, date: str, checkpoints: dict) -> StepResult:
    """Draft generation stage.

    Takes normalized data + discussion output and produces a markdown draft.
    """
    output_dir = WORKSPACE / "data" / "worker" / task_type / date
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "draft.md"

    # Scaffold: produce a stub draft
    draft_content = (
        f"# {task_type.title()} Report — {date}\n\n"
        f"<!-- Generated by orchestrator worker at {_now_iso()} -->\n"
        f"<!-- Placeholder: real content generated by Claude Code in Phase 2 -->\n"
    )
    output_path.write_text(draft_content, encoding="utf-8")

    return StepResult(
        step_name="write",
        status="ok",
        output_path=str(output_path),
        timestamp=_now_iso(),
    )


def _review(task_type: str, date: str, checkpoints: dict) -> StepResult:
    """Quality review stage.

    Checks the draft against structural and content quality rules.
    Returns review feedback for the revise stage.
    """
    output_dir = WORKSPACE / "data" / "worker" / task_type / date
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "review.json"

    review = {
        "task_type": task_type,
        "date": date,
        "passed": True,  # scaffold — real checks in Phase 2
        "issues": [],
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
    """Revision stage — apply review feedback to the draft.

    If review passed, this is a no-op that promotes the draft to final.
    If review found issues, Claude Code revises based on feedback.
    """
    write_output = checkpoints.get("write", {}).get("output_path", "")
    review_output = checkpoints.get("review", {}).get("output_path", "")

    output_dir = WORKSPACE / "data" / "worker" / task_type / date
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / "final.md"

    # Scaffold: copy draft as final (real revision in Phase 2)
    if write_output and Path(write_output).exists():
        final_path.write_text(
            Path(write_output).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    return StepResult(
        step_name="revise",
        status="ok",
        output_path=str(final_path),
        timestamp=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _discussion_roles(task_type: str) -> list[str]:
    """Return role names for the discussion stage."""
    if task_type == "weekly":
        return [
            "module_macro",
            "module_structure",
            "module_sectors",
            "module_watchlist",
            "module_risk",
            "cross_challenge",
            "editor_ruling",
        ]
    return ["bull", "bear", "quant", "risk_officer", "editor"]


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


_STAGE_HANDLERS = {
    "normalize": _normalize,
    "discuss": _discuss,
    "write": _write,
    "review": _review,
    "revise": _revise,
}
