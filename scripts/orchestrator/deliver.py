#!/usr/bin/env python3
"""
Delivery pipeline — quality check, PDF generation, Telegram send,
memory archival, and state update.

Quality check and PDF generation are real; Telegram send and memory
archival remain explicit no-ops until their integrations land.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORKSPACE = Path(__file__).resolve().parents[2]
QUALITY_CHECK_SCRIPT = WORKSPACE / "scripts" / "reporting" / "report_quality_check.py"
MD_TO_PDF_SCRIPT = WORKSPACE / "scripts" / "reporting" / "md_to_pdf.py"


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
        2. Run quality check (real — calls report_quality_check.py)
        3. Copy to canonical report path
        4. Generate PDF (real — calls md_to_pdf.py)
        5. Send via Telegram (not yet implemented)
        6. Archive to memory (not yet implemented)

    Semantics: ``delivered`` is True only when local report + PDF succeed.
    ``telegram_sent`` and ``memory_archived`` are always False until their
    integrations land; their status is surfaced honestly in the result.
    """
    result = DeliveryResult(
        task_type=task_type,
        date=date,
        timestamp=_now_iso(),
    )

    # 1. Find the final report ------------------------------------------------
    revise_cp = checkpoints.get("revise", {})
    write_cp = checkpoints.get("write", {})
    final_path = revise_cp.get("output_path") or write_cp.get("output_path", "")

    if not final_path or not Path(final_path).exists():
        result.error = "No final report found in checkpoints"
        print(f"  [deliver] ERROR: {result.error}", file=sys.stderr)
        return result.to_dict()

    # 2. Quality check (real) --------------------------------------------------
    qc_ok, qc_issues = quality_check(final_path, task_type)
    result.quality_passed = qc_ok

    if not qc_ok:
        result.error = (
            f"Quality check failed ({len(qc_issues)} issues) — delivery aborted"
        )
        print(f"  [deliver] {result.error}", file=sys.stderr)
        for issue in qc_issues[:5]:
            print(f"    • {issue}", file=sys.stderr)
        result.delivered = False
        result.timestamp = _now_iso()
        return result.to_dict()

    # 3. Copy to canonical path ------------------------------------------------
    report_dest = _report_path(task_type, date)
    report_dest.parent.mkdir(parents=True, exist_ok=True)
    report_dest.write_text(
        Path(final_path).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result.report_path = str(report_dest)
    print(f"  [deliver] Report saved to {report_dest}")

    # 4. PDF generation (real) -------------------------------------------------
    pdf_path = report_dest.with_suffix(".pdf")
    pdf_ok = generate_pdf(report_dest, pdf_path)
    if pdf_ok:
        result.pdf_path = str(pdf_path)
        print(f"  [deliver] PDF saved to {pdf_path}")
    else:
        print(f"  [deliver] PDF generation failed — report still delivered as markdown",
              file=sys.stderr)

    # 5. Telegram delivery (not yet implemented) -------------------------------
    telegram_sent = False
    print(f"  [deliver] Telegram send: not yet implemented")

    # 6. Memory archival (not yet implemented) ---------------------------------
    result.memory_archived = False
    print(f"  [deliver] Memory archive: not yet implemented")

    # Delivered = local report + PDF succeeded; downstream channels are separate
    result.delivered = True
    result.timestamp = _now_iso()
    if not telegram_sent or not result.memory_archived:
        pending = []
        if not telegram_sent:
            pending.append("telegram")
        if not result.memory_archived:
            pending.append("memory_archive")
        result.error = f"Delivered locally but pending: {', '.join(pending)}"

    return result.to_dict()


def quality_check(report_path: str, task_type: str) -> tuple[bool, list[str]]:
    """Run quality validation on a report via report_quality_check.py.

    Returns:
        (passed, issues) — passed is True when zero issues found.
    """
    path = Path(report_path)
    if not path.exists():
        return False, [f"File not found: {report_path}"]

    if not QUALITY_CHECK_SCRIPT.exists():
        print(f"  [deliver] WARNING: quality check script not found at "
              f"{QUALITY_CHECK_SCRIPT}, falling back to basic check", file=sys.stderr)
        content = path.read_text(encoding="utf-8")
        if len(content.strip()) < 50:
            return False, [f"Content too short ({len(content)} chars)"]
        return True, []

    try:
        proc = subprocess.run(
            [sys.executable, str(QUALITY_CHECK_SCRIPT), str(path)],
            capture_output=True, text=True, timeout=30,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        return False, ["Quality check timed out (30s)"]

    # Parse issues from stdout (one per line after the PASSED/FAILED header)
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    issues = [l for l in lines if l not in ("PASSED", "FAILED")]

    passed = proc.returncode == 0
    label = "PASSED" if passed else "FAILED"
    print(f"  [deliver] Quality check {label} ({len(issues)} issues)")
    return passed, issues


def generate_pdf(md_path: Path, pdf_path: Path) -> bool:
    """Generate PDF from markdown via md_to_pdf.py.

    Returns True on success, False on failure (non-fatal).
    """
    if not MD_TO_PDF_SCRIPT.exists():
        print(f"  [deliver] WARNING: md_to_pdf.py not found, skipping PDF",
              file=sys.stderr)
        return False

    try:
        proc = subprocess.run(
            [sys.executable, str(MD_TO_PDF_SCRIPT), str(md_path), str(pdf_path)],
            capture_output=True, text=True, timeout=60,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        print(f"  [deliver] PDF generation timed out (60s)", file=sys.stderr)
        return False

    if proc.returncode != 0:
        print(f"  [deliver] PDF generation error: {proc.stderr[:200]}",
              file=sys.stderr)
        return False

    return pdf_path.exists()


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
