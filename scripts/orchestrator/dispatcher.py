#!/usr/bin/env python3
"""
Trade task dispatcher — routes task types through staged pipelines.

Usage:
    python3 scripts/orchestrator/dispatcher.py <task_type> [--date YYYY-MM-DD] \
        [--stage collect|worker|deliver|all] [--dry-run]

Stages: collect → normalize → discuss → write → review → revise → deliver
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parents[2]
STATE_DIR = WORKSPACE / ".state" / "tasks"

STAGES = ["collect", "normalize", "discuss", "write", "review", "revise", "deliver"]

VALID_TASK_TYPES = [
    "premarket",
    "closing",
    "intraday",
    "weekly",
    "philosophy",
]


# ---------------------------------------------------------------------------
# Task state persistence
# ---------------------------------------------------------------------------

class TaskState:
    """Durable run-state for a single task execution."""

    def __init__(self, task_type: str, date: str):
        self.task_type = task_type
        self.date = date
        self.path = STATE_DIR / f"{task_type}-{date}.json"
        self._data: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {
                "task_type": self.task_type,
                "date": self.date,
                "status": "pending",
                "current_stage": None,
                "checkpoints": {},
                "errors": [],
                "started_at": None,
                "updated_at": None,
            }

    def save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._data["updated_at"] = _now_iso()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    # -- accessors --

    @property
    def status(self) -> str:
        return self._data["status"]

    @status.setter
    def status(self, value: str):
        self._data["status"] = value

    @property
    def current_stage(self) -> str | None:
        return self._data["current_stage"]

    @current_stage.setter
    def current_stage(self, value: str | None):
        self._data["current_stage"] = value

    @property
    def checkpoints(self) -> dict:
        return self._data["checkpoints"]

    @property
    def errors(self) -> list:
        return self._data["errors"]

    def mark_started(self):
        self._data["started_at"] = _now_iso()
        self.status = "in_progress"
        self.save()

    def checkpoint(self, stage: str, result: dict):
        self.checkpoints[stage] = {
            "completed_at": _now_iso(),
            **result,
        }
        self.save()

    def record_error(self, stage: str, error: str):
        self.errors.append({
            "stage": stage,
            "error": error,
            "timestamp": _now_iso(),
        })
        self.status = "failed"
        self.current_stage = stage
        self.save()

    def mark_completed(self):
        self.status = "completed"
        self.current_stage = None
        self.save()

    def completed_stages(self) -> list[str]:
        return [s for s in STAGES if s in self.checkpoints]

    def next_stage(self, from_stage: str | None = None) -> str | None:
        """Return the next stage to run, respecting checkpoints."""
        completed = set(self.completed_stages())
        for stage in STAGES:
            if from_stage and STAGES.index(stage) < STAGES.index(from_stage):
                continue
            if stage not in completed:
                return stage
        return None


# ---------------------------------------------------------------------------
# Dispatch logic
# ---------------------------------------------------------------------------

def dispatch(task_type: str, date: str, stage: str = "all", dry_run: bool = False):
    """Run a task through its pipeline stages."""
    from scripts.orchestrator.collect import run_collection
    from scripts.orchestrator.worker import run_worker
    from scripts.orchestrator.deliver import run_delivery

    state = TaskState(task_type, date)

    if stage == "all":
        target_stage = state.next_stage()
    else:
        target_stage = stage

    if target_stage is None:
        print(f"[dispatcher] Task {task_type}/{date} already completed.")
        return state

    print(f"[dispatcher] task={task_type} date={date} stage={target_stage} dry_run={dry_run}")

    if dry_run:
        print(f"[dispatcher] DRY RUN — would execute from stage '{target_stage}'")
        _print_plan(task_type, target_stage, stage == "all")
        return state

    state.mark_started()

    stages_to_run = _resolve_stages(target_stage, run_all=(stage == "all"))

    for s in stages_to_run:
        state.current_stage = s
        state.save()
        print(f"\n[dispatcher] === Stage: {s} ===")

        try:
            if s == "collect":
                result = run_collection(task_type, date)
                state.checkpoint(s, {"manifest_path": result.get("manifest_path", ""),
                                     "coverage": result.get("coverage_ratio", 0)})
            elif s in ("normalize", "discuss", "write", "review", "revise"):
                result = run_worker(task_type, date, s, state.checkpoints)
                state.checkpoint(s, {"output_path": result.get("output_path", "")})
            elif s == "deliver":
                result = run_delivery(task_type, date, state.checkpoints)
                state.checkpoint(s, {"delivered": result.get("delivered", False)})
            else:
                raise ValueError(f"Unknown stage: {s}")
        except Exception as exc:
            state.record_error(s, str(exc))
            print(f"[dispatcher] FAILED at stage '{s}': {exc}", file=sys.stderr)
            raise

    state.mark_completed()
    print(f"\n[dispatcher] Task {task_type}/{date} completed successfully.")
    return state


def _resolve_stages(from_stage: str, run_all: bool) -> list[str]:
    """Return ordered list of stages to execute."""
    idx = STAGES.index(from_stage)
    if run_all:
        return STAGES[idx:]
    return [from_stage]


def _print_plan(task_type: str, from_stage: str, run_all: bool):
    """Show what would run without executing."""
    stages = _resolve_stages(from_stage, run_all)
    print(f"[plan] Task type: {task_type}")
    print(f"[plan] Stages to execute: {' → '.join(stages)}")

    manifest_path = WORKSPACE / "scripts" / "orchestrator" / "manifests" / f"{task_type}.json"
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        print(f"[plan] Collection scripts: {len(manifest.get('scripts', []))}")
    else:
        print(f"[plan] No manifest found at {manifest_path}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Trade task dispatcher",
        usage="python3 scripts/orchestrator/dispatcher.py <task_type> [options]",
    )
    parser.add_argument("task_type", choices=VALID_TASK_TYPES,
                        help="Task type to dispatch")
    parser.add_argument("--date", default=None,
                        help="Task date in YYYY-MM-DD (default: today Shanghai time)")
    parser.add_argument("--stage", default="all",
                        choices=["collect", "worker", "deliver", "all",
                                 *STAGES],
                        help="Stage to execute (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without executing")

    args = parser.parse_args()

    if args.date is None:
        args.date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    # Ensure imports work from workspace root
    sys.path.insert(0, str(WORKSPACE))

    dispatch(args.task_type, args.date, stage=args.stage, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
