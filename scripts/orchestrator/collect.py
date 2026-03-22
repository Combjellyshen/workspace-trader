#!/usr/bin/env python3
"""
Collection pipeline — runs data scripts per manifest and validates coverage.

Each task type has a JSON manifest under manifests/ listing commands, whether
they are required, and their timeout. The collector executes them with
subprocess and writes a result manifest alongside the output.
"""

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = Path(__file__).resolve().parent / "manifests"


@dataclass
class ScriptResult:
    name: str
    command: str
    required: bool
    status: str  # "ok" | "error" | "timeout" | "skipped"
    duration_s: float = 0.0
    error: str = ""
    output_lines: int = 0


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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def load_manifest(task_type: str) -> dict:
    """Load the JSON manifest for a given task type."""
    path = MANIFESTS_DIR / f"{task_type}.json"
    if not path.exists():
        raise FileNotFoundError(f"No manifest for task type '{task_type}' at {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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
        timeout_s = spec.get("timeout_s", 60)

        sr = ScriptResult(name=name, command=command, required=required, status="ok")
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

            if proc.returncode != 0:
                sr.status = "error"
                sr.error = (proc.stderr or "")[:500]
                result.errors.append(f"{name}: exit {proc.returncode}")
                print(f"  [collect] FAIL {name}: exit {proc.returncode}", file=sys.stderr)
            else:
                sr.output_lines = len((proc.stdout or "").splitlines())
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

    # Coverage gate
    if result.coverage_ratio < min_coverage:
        print(f"  [collect] WARNING: coverage {result.coverage_ratio:.0%} < "
              f"minimum {min_coverage:.0%} — task degraded", file=sys.stderr)

    print(f"  [collect] Done: {required_ok}/{required_total} required OK, "
          f"coverage={result.coverage_ratio:.0%}")

    return result.to_dict()


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/orchestrator/collect.py <task_type> <date>")
        sys.exit(1)
    res = run_collection(sys.argv[1], sys.argv[2])
    print(json.dumps(res, indent=2, ensure_ascii=False))
