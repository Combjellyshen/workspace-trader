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
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parents[2]
STATE_DIR = WORKSPACE / ".state" / "tasks"

STAGES = ["collect", "normalize", "discuss", "write", "review", "revise", "deliver"]

_NOTIFY_STATUSES = {"done", "failed", "skipped"}

VALID_TASK_TYPES = [
    "premarket",
    "closing",
    "weekly",
    "philosophy",
    "scout",
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
        _notify_stage(self.task_type, self.date, stage, "done",
                      note=result.get("note", ""),
                      completed=len(self.completed_stages()))

    def record_error(self, stage: str, error: str):
        self.errors.append({
            "stage": stage,
            "error": error,
            "timestamp": _now_iso(),
        })
        self.status = "failed"
        self.current_stage = stage
        self.save()
        _notify_stage(self.task_type, self.date, stage, "failed",
                      note=error[:200])

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
# Telegram progress notification
# ---------------------------------------------------------------------------

def _notify_stage(task_type: str, date: str, stage: str, status: str,
                  note: str = "", completed: int = 0) -> None:
    """Best-effort Telegram notification on stage transitions."""
    if status not in _NOTIFY_STATUSES:
        return
    tg_notify = Path(__file__).resolve().parent / "tg_notify.py"
    if not tg_notify.exists():
        return
    try:
        # Compute total expected stages for this task type
        from scripts.orchestrator.worker import flow_stages
        valid_worker = flow_stages(task_type) or WORKER_STAGES
        total = len([s for s in STAGES if s not in WORKER_STAGES or s in valid_worker])

        cmd = [
            sys.executable, str(tg_notify),
            "--task-type", task_type,
            "--date", date,
            "--stage", stage,
            "--status", status,
            "--total-stages", str(total),
            "--completed-stages", str(completed),
        ]
        if note:
            cmd.extend(["--note", note[:200]])
        subprocess.Popen(cmd, cwd=WORKSPACE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Dispatch logic
# ---------------------------------------------------------------------------

WORKER_STAGES = ["normalize", "discuss", "write", "review", "revise"]


def dispatch(task_type: str, date: str, stage: str = "all", dry_run: bool = False):
    """Run a task through its pipeline stages."""
    from scripts.orchestrator.collect import run_collection
    from scripts.orchestrator.worker import run_worker
    from scripts.orchestrator.deliver import run_delivery

    state = TaskState(task_type, date)

    stages_to_run = _resolve_stages(stage, state)

    if not stages_to_run:
        print(f"[dispatcher] Task {task_type}/{date} already completed.")
        return state

    print(f"[dispatcher] task={task_type} date={date} stage={stage} "
          f"stages={' → '.join(stages_to_run)} dry_run={dry_run}")

    if dry_run:
        print(f"[dispatcher] DRY RUN — would execute stages: {' → '.join(stages_to_run)}")
        _print_plan(task_type, stages_to_run)
        return state

    state.mark_started()

    for s in stages_to_run:
        state.current_stage = s
        state.save()
        print(f"\n[dispatcher] === Stage: {s} ===")

        try:
            if s == "collect":
                result = run_collection(task_type, date)
                if result.get("status") == "error":
                    raise RuntimeError(result.get("error", f"{s} stage failed"))
                state.checkpoint(s, {"manifest_path": result.get("manifest_path", ""),
                                     "coverage": result.get("coverage_ratio", 0)})
            elif s in WORKER_STAGES:
                result = run_worker(task_type, date, s, state.checkpoints)
                if result.get("status") == "error":
                    raise RuntimeError(result.get("error", f"{s} stage failed"))
                state.checkpoint(s, {"output_path": result.get("output_path", "")})
            elif s == "deliver":
                result = run_delivery(task_type, date, state.checkpoints)
                if result.get("error") and not result.get("delivered", False):
                    raise RuntimeError(result.get("error"))
                state.checkpoint(s, {"delivered": result.get("delivered", False)})
            else:
                raise ValueError(f"Unknown stage: {s}")
        except Exception as exc:
            state.record_error(s, str(exc))
            print(f"[dispatcher] FAILED at stage '{s}': {exc}", file=sys.stderr)
            raise

    # Mark completed if every *expected* stage for this task type has a checkpoint.
    # Build the effective stage list the same way _resolve_stages() does:
    # start with STAGES, drop worker stages not in this task type's flow.
    from scripts.orchestrator.worker import flow_stages
    valid_worker = flow_stages(task_type) or WORKER_STAGES
    expected = [s for s in STAGES if s not in WORKER_STAGES or s in valid_worker]

    if state.status != "failed" and all(s in state.checkpoints for s in expected):
        state.mark_completed()
        print(f"\n[dispatcher] Task {task_type}/{date} completed successfully.")
    else:
        state.status = "in_progress"
        state.current_stage = None
        state.save()
        done = ", ".join(state.completed_stages())
        missing = ", ".join(s for s in expected if s not in state.checkpoints)
        print(f"\n[dispatcher] Partial run finished. Completed: {done}  Missing: {missing}")

    return state


def _resolve_stages(stage: str, state: "TaskState") -> list[str]:
    """Return ordered list of stages to execute.

    For "all" and "worker" modes the stages are filtered through the task
    type's WorkerFlow so that only declared stages are scheduled (e.g.
    premarket has no 'discuss' step and will skip it).
    """
    from scripts.orchestrator.worker import flow_stages

    task_type = state.task_type
    valid_worker = flow_stages(task_type) or WORKER_STAGES

    if stage == "all":
        # Resume from first incomplete stage, but only include worker
        # stages that exist in this task type's flow.
        first = state.next_stage()
        if first is None:
            return []
        result = []
        for s in STAGES:
            if STAGES.index(s) < STAGES.index(first):
                continue
            if s in WORKER_STAGES and s not in valid_worker:
                continue
            result.append(s)
        return result
    if stage == "worker":
        return [s for s in valid_worker if s not in state.checkpoints]
    # Single named stage
    return [stage]


def _print_plan(task_type: str, stages: list[str]):
    """Show what would run without executing."""
    from scripts.orchestrator.worker import flow_stages

    print(f"[plan] Task type: {task_type}")
    print(f"[plan] Stages to execute: {' → '.join(stages)}")

    valid_worker = flow_stages(task_type)
    if valid_worker:
        print(f"[plan] Worker flow: {' → '.join(valid_worker)}")

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
