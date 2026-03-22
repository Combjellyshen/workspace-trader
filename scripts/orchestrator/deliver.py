#!/usr/bin/env python3
"""
Delivery pipeline — quality check, PDF generation, Telegram send,
memory archival, and state update.

Current implementation: scaffolded with clear function signatures.
Real delivery integration will be added in Phase 4.
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parents[2]


@dataclass
class DeliveryResult:
    """Outcome of the delivery pipeline."""
    task_type: str
    date: str
    delivered: bool = False
    report_path: str = ""
    pdf_path: str = ""
    quality_passed: bool = False
    memory_archived: bool = False
    error: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "date": self.date,
            "delivered": self.delivered,
            "report_path": self.report_path,
            "pdf_path": self.pdf_path,
            "quality_passed": self.quality_passed,
            "memory_archived": self.memory_archived,
            "error": self.error,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Output path conventions
# ---------------------------------------------------------------------------

REPORT_PATHS = {
    "premarket": "reports/daily/{date}-pre-market.md",
    "closing": "reports/daily/{date}-closing.md",
    "weekly": "reports/weekly/{date}-market-insight.md",
    "philosophy": "reports/philosophy/{date}-philosophy.md",
}


def _report_path(task_type: str, date: str) -> Path:
    template = REPORT_PATHS.get(task_type, f"reports/{task_type}/{date}.md")
    return WORKSPACE / template.replace("{date}", date)


# ---------------------------------------------------------------------------
# Delivery pipeline
# ---------------------------------------------------------------------------

def run_delivery(task_type: str, date: str, checkpoints: dict) -> dict:
    """Execute the full delivery pipeline.

    Steps:
        1. Locate final report from worker checkpoints
        2. Run quality check
        3. Copy to canonical report path
        4. Generate PDF (scaffold)
        5. Send via Telegram (scaffold)
        6. Archive to memory (scaffold)

    Args:
        task_type: Task type name.
        date: Task date (YYYY-MM-DD).
        checkpoints: Completed stage checkpoints from TaskState.

    Returns:
        Dict with delivery result fields.
    """
    result = DeliveryResult(
        task_type=task_type,
        date=date,
        timestamp=_now_iso(),
    )

    # 1. Find the final report
    revise_cp = checkpoints.get("revise", {})
    write_cp = checkpoints.get("write", {})
    final_path = revise_cp.get("output_path") or write_cp.get("output_path", "")

    if not final_path or not Path(final_path).exists():
        result.error = "No final report found in checkpoints"
        print(f"  [deliver] ERROR: {result.error}", file=sys.stderr)
        return result.to_dict()

    # 2. Quality check (scaffold — calls real quality check in Phase 4)
    result.quality_passed = quality_check(final_path, task_type)

    # 3. Copy to canonical path
    report_dest = _report_path(task_type, date)
    report_dest.parent.mkdir(parents=True, exist_ok=True)
    report_dest.write_text(
        Path(final_path).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result.report_path = str(report_dest)
    print(f"  [deliver] Report saved to {report_dest}")

    # 4. PDF generation (scaffold)
    result.pdf_path = ""  # filled in Phase 4

    # 5. Telegram delivery (scaffold)
    print(f"  [deliver] Telegram send: not yet implemented (Phase 4)")

    # 6. Memory archival (scaffold)
    result.memory_archived = False  # filled in Phase 4
    print(f"  [deliver] Memory archive: not yet implemented (Phase 4)")

    result.delivered = True
    result.timestamp = _now_iso()
    return result.to_dict()


def quality_check(report_path: str, task_type: str) -> bool:
    """Run quality validation on a report.

    Scaffold: checks that the file exists and has non-trivial content.
    Phase 4 will integrate scripts/reporting/report_quality_check.py.
    """
    path = Path(report_path)
    if not path.exists():
        print(f"  [deliver] Quality check FAIL: file not found", file=sys.stderr)
        return False

    content = path.read_text(encoding="utf-8")
    if len(content.strip()) < 50:
        print(f"  [deliver] Quality check FAIL: content too short ({len(content)} chars)",
              file=sys.stderr)
        return False

    print(f"  [deliver] Quality check passed ({len(content)} chars)")
    return True


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
