#!/usr/bin/env python3
"""
Collection pipeline — runs data scripts per manifest and validates coverage.

Key features:
  - Reuses recent artifacts: if the same data was collected earlier today
    (or within a configurable window), skips re-running the script.
  - Searches daily_inputs for existing data from this week when running
    weekly tasks, so weekly reports build on daily data rather than
    re-fetching everything from scratch.
  - Non-required scripts use short timeouts to fail fast.
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = Path(__file__).resolve().parent / "manifests"
SH_TZ = ZoneInfo("Asia/Shanghai")

# How old an existing artifact can be before we re-fetch (hours)
# Dynamic per task type: weekly tasks get a longer window so weekend runs
# can reuse Friday's data; daily tasks use a tighter window.
REUSE_MAX_AGE_H_BY_TYPE: dict[str, int] = {
    "weekly": 72,     # 3 days — covers weekends
    "closing": 2,     # 2 hours — closing 必须用收盘后新数据，不能复用盘前
    "scout": 4,       # 4 hours — 选股需要较新数据
    "premarket": 12,  # 12 hours — 盘前可以复用昨天收盘的
}
REUSE_MAX_AGE_H_DEFAULT = 12
# Non-required scripts get a shorter timeout to fail fast,
# but must still be long enough for data-fetching scripts (API/scraping).
NON_REQUIRED_TIMEOUT_CAP = 180


@dataclass
class ScriptResult:
    name: str
    command: str
    required: bool
    status: str  # "ok" | "error" | "timeout" | "skipped" | "reused"
    duration_s: float = 0.0
    error: str = ""
    stderr_snippet: str = ""
    output_lines: int = 0
    output_file: str = ""


@dataclass
class CollectionResult:
    task_type: str
    date: str
    scripts: list[ScriptResult] = field(default_factory=list)
    coverage_ratio: float = 0.0
    manifest_path: str = ""
    started_at: str = ""
    completed_at: str = ""
    errors: list[str] = field(default_factory=list)
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_manifest(task_type: str) -> dict:
    """Load the JSON manifest for a given task type."""
    path = MANIFESTS_DIR / f"{task_type}.json"
    if not path.exists():
        raise FileNotFoundError(f"No manifest for task type '{task_type}' at {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find_recent_artifact(name: str, output_dir: Path, date: str,
                          task_type: str = "daily") -> Path | None:
    """Look for a recent artifact file that can be reused.

    Search order:
      1. Same output_dir (exact match from earlier run today)
      2. daily_inputs/{date}/ (reuse from daily precollect)
      3. daily_inputs from the last 7 days (reuse from any recent day)

    The staleness window is task-type-aware: weekly tasks tolerate older
    artifacts (72 h) so that weekend runs can reuse Friday's data.

    Returns the path if found and fresh enough, else None.
    """
    candidates = []

    # 1. Exact match in current output dir
    exact = output_dir / f"{name}.txt"
    if exact.exists():
        candidates.append(exact)

    # 2. Same-day daily_inputs
    daily_today = WORKSPACE / "data" / "daily_inputs" / date / f"{name}.txt"
    if daily_today.exists() and daily_today != exact:
        candidates.append(daily_today)

    # 3. Recent daily_inputs (last 7 days)
    daily_base = WORKSPACE / "data" / "daily_inputs"
    if daily_base.exists():
        now = datetime.now(SH_TZ)
        for d in sorted(daily_base.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            f = d / f"{name}.txt"
            if f.exists() and f not in candidates:
                candidates.append(f)
            if len(candidates) >= 5:
                break

    # Pick the newest candidate that's within the reuse window
    max_age_h = REUSE_MAX_AGE_H_BY_TYPE.get(task_type, REUSE_MAX_AGE_H_DEFAULT)
    cutoff = time.time() - max_age_h * 3600
    for c in candidates:
        try:
            if os.path.getmtime(str(c)) >= cutoff:
                return c
        except OSError:
            continue

    return None


def run_collection(task_type: str, date: str) -> dict:
    """Execute all collection scripts for a task type.

    Returns a dict with keys: manifest_path, coverage_ratio, errors, items.
    """
    manifest = load_manifest(task_type)
    scripts_spec = manifest.get("scripts", [])
    min_coverage = manifest.get("min_coverage", 0.7)
    output_dir_tpl = manifest.get("output_dir", "data/daily_inputs/{date}/")
    output_dir = WORKSPACE / output_dir_tpl.replace("{date}", date)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = CollectionResult(
        task_type=task_type,
        date=date,
        started_at=_now_iso(),
    )

    required_total = sum(1 for s in scripts_spec if s.get("required", False))
    required_ok = 0

    for spec in scripts_spec:
        name = spec["name"]
        command = spec["command"]
        required = spec.get("required", False)
        timeout_s = spec.get("timeout_s") or spec.get("timeout") or 60

        # Cap non-required timeouts to fail fast
        if not required and timeout_s > NON_REQUIRED_TIMEOUT_CAP:
            timeout_s = NON_REQUIRED_TIMEOUT_CAP

        sr = ScriptResult(name=name, command=command, required=required, status="ok")
        artifact_path = output_dir / f"{name}.txt"

        # --- Try to reuse existing artifact ---
        cached = _find_recent_artifact(name, output_dir, date, task_type=task_type)
        if cached is not None:
            try:
                content = cached.read_text(encoding="utf-8")
                sr.output_lines = len(content.splitlines())
                # Copy to output_dir if not already there
                if cached != artifact_path:
                    artifact_path.write_text(content, encoding="utf-8")
                sr.output_file = str(artifact_path)
                sr.status = "reused"
                sr.duration_s = 0.0
                if required:
                    required_ok += 1
                print(f"  [collect] REUSE {name} ({sr.output_lines} lines, from {cached.parent.name}/)")
                result.scripts.append(sr)
                continue
            except OSError:
                pass  # fall through to fresh fetch

        # --- Fresh fetch ---
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(WORKSPACE),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            sr.duration_s = round(time.monotonic() - t0, 2)

            if proc.stderr:
                sr.stderr_snippet = proc.stderr[:500]

            if proc.returncode != 0:
                sr.status = "error"
                sr.error = (proc.stderr or "")[:500]
                result.errors.append(f"{name}: exit {proc.returncode}")
                print(f"  [collect] FAIL {name}: exit {proc.returncode} ({sr.duration_s}s)", file=sys.stderr)
            else:
                stdout = proc.stdout or ""
                sr.output_lines = len(stdout.splitlines())
                artifact_path.write_text(stdout, encoding="utf-8")
                sr.output_file = str(artifact_path)
                if required:
                    required_ok += 1
                print(f"  [collect] OK   {name} ({sr.duration_s}s, {sr.output_lines} lines)")

        except subprocess.TimeoutExpired:
            sr.duration_s = round(time.monotonic() - t0, 2)
            sr.status = "timeout"
            sr.error = f"Timed out after {timeout_s}s"
            result.errors.append(f"{name}: timeout")
            print(f"  [collect] TIMEOUT {name} ({timeout_s}s)", file=sys.stderr)

        except Exception as exc:
            sr.duration_s = round(time.monotonic() - t0, 2)
            sr.status = "error"
            sr.error = str(exc)[:500]
            result.errors.append(f"{name}: {exc}")
            print(f"  [collect] ERROR {name}: {exc}", file=sys.stderr)

        result.scripts.append(sr)

    # Coverage calculation
    if required_total > 0:
        result.coverage_ratio = round(required_ok / required_total, 3)
    else:
        result.coverage_ratio = 1.0

    result.completed_at = _now_iso()

    # Write result manifest
    manifest_out_path = output_dir / f"{task_type}-manifest.json"
    with open(manifest_out_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
    result.manifest_path = str(manifest_out_path)

    # Stats
    reused = sum(1 for s in result.scripts if s.status == "reused")
    fresh = sum(1 for s in result.scripts if s.status == "ok")
    failed = sum(1 for s in result.scripts if s.status in ("error", "timeout"))

    if result.coverage_ratio < min_coverage:
        result.status = "error"
        result.errors.append(
            f"Coverage {result.coverage_ratio:.0%} below minimum {min_coverage:.0%}")
        print(f"  [collect] WARNING: coverage {result.coverage_ratio:.0%} < "
              f"minimum {min_coverage:.0%} — task degraded", file=sys.stderr)

    print(f"  [collect] Done: {required_ok}/{required_total} required OK, "
          f"coverage={result.coverage_ratio:.0%} "
          f"(reused={reused}, fresh={fresh}, failed={failed})")

    return result.to_dict()


def _now_iso() -> str:
    return datetime.now(SH_TZ).isoformat()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/orchestrator/collect.py <task_type> <date>")
        sys.exit(1)
    res = run_collection(sys.argv[1], sys.argv[2])
    print(json.dumps(res, indent=2, ensure_ascii=False))
