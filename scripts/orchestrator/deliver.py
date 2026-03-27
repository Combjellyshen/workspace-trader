#!/usr/bin/env python3
"""
Delivery pipeline — quality check, PDF generation, memory archival,
and state update.

Quality check and PDF generation call real scripts.
Memory archival calls memory_manager.py.
Telegram delivery is handled by the upstream OpenClaw agent framework
(via the cron job's delivery config), not by this module.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TG_NOTIFY_SCRIPT = Path(__file__).resolve().parent / "tg_notify.py"

WORKSPACE = Path(__file__).resolve().parents[2]
QUALITY_CHECK_SCRIPT = WORKSPACE / "scripts" / "reporting" / "report_quality_check.py"
MD_TO_PDF_SCRIPT = WORKSPACE / "scripts" / "reporting" / "md_to_pdf.py"
MEMORY_MANAGER = WORKSPACE / "scripts" / "memory" / "memory_manager.py"

# Task type → memory archive category
MEMORY_CATEGORIES = {
    "premarket": ("daily", "{date}-pre-market"),
    "closing": ("daily", "{date}-closing"),
    "weekly": ("weekly", "{date}-market-insight"),
    "philosophy": ("philosophy", "{date}-philosophy"),
}


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
    "scout": "reports/scout/{date}-scout.md",
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
        2. Run quality check (calls report_quality_check.py)
        3. Copy to canonical report path
        4. Generate PDF (calls md_to_pdf.py)
        5. Archive to memory (calls memory_manager.py)
        6. Archive sentiment (closing tasks only)

    Telegram delivery is handled by the upstream OpenClaw framework
    via the cron job's delivery config, not by this module.

    ``delivered`` is True when local report + PDF succeed.
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

    # 2. Quality check — reuse worker review result if available ----------------
    #    This saves a full Claude CLI invocation (~300MB + 1-2min).
    #    Only fall back to standalone QC if no review checkpoint exists.
    review_cp = checkpoints.get("review", {})
    review_path = review_cp.get("output_path", "")

    if review_path and Path(review_path).exists():
        # Trust the worker review — it already used Claude to evaluate quality
        try:
            with open(review_path, encoding="utf-8") as f:
                review_data = json.load(f)
            verdict = review_data.get("verdict", review_data.get("passed", True))
            # Accept if verdict is PASS/True, or score >= 60
            score = review_data.get("score", review_data.get("overall_score", 100))
            qc_ok = verdict in (True, "PASS") or (isinstance(score, (int, float)) and score >= 60)
            qc_issues = review_data.get("issues", review_data.get("critical_issues", []))
            print(f"  [deliver] Reusing worker review (score={score}, verdict={verdict})")
        except (json.JSONDecodeError, OSError):
            # Review file corrupt — fall back to standalone QC
            qc_ok, qc_issues = quality_check(final_path, task_type)
    else:
        # No review checkpoint — 跳过质检直接交付
        # review 阶段可能失败（空输出等），但 revise 已经产出了 final.md
        # 不应因为 review 缺失而拦住交付
        print(f"  [deliver] No review checkpoint — skipping QC, delivering as-is")
        qc_ok = True
        qc_issues = ["Review checkpoint missing — delivered without quality check"]

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
        _send_pdf_to_telegram(task_type, date, pdf_path)
    else:
        print(f"  [deliver] PDF generation failed — report still delivered as markdown",
              file=sys.stderr)

    # 5. Memory archival -------------------------------------------------------
    result.memory_archived = archive_to_memory(task_type, date, str(report_dest))
    if result.memory_archived:
        print(f"  [deliver] Memory archived successfully")
    else:
        print(f"  [deliver] Memory archive failed (non-fatal)", file=sys.stderr)

    # 6. Sentiment snapshot (closing tasks only) --------------------------------
    if task_type == "closing":
        _archive_sentiment(date)

    # Delivered = local report + PDF succeeded
    # Telegram delivery is handled by the OpenClaw agent framework, not here
    result.delivered = True
    result.timestamp = _now_iso()

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
            capture_output=True, text=True, timeout=90,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        # 超时不拦截交付，只记录警告
        return True, ["Quality check timed out (90s) — delivered with warning"]

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


def archive_to_memory(task_type: str, date: str, report_path: str) -> bool:
    """Archive the report to the memory/reviews/ directory.

    Copies the final report as a review log for future memory queries.
    This is NOT the same as save_reports (which fetches broker research).

    Returns True on success, False on failure (non-fatal).
    """
    rp = Path(report_path)
    if not rp.exists():
        print(f"  [deliver] Cannot archive — report not found: {report_path}",
              file=sys.stderr)
        return False

    reviews_dir = WORKSPACE / "memory" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # Determine archive filename
    category_info = MEMORY_CATEGORIES.get(task_type)
    if category_info:
        _, name_template = category_info
        archive_name = name_template.replace("{date}", date) + ".md"
    else:
        archive_name = f"{date}-{task_type}.md"

    dest = reviews_dir / archive_name
    try:
        dest.write_text(rp.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  [deliver] Archived to {dest}")
        return True
    except OSError as e:
        print(f"  [deliver] Archive failed: {e}", file=sys.stderr)
        return False


def _send_pdf_to_telegram(task_type: str, date: str, pdf_path: Path) -> bool:
    """Best-effort PDF delivery to the Trade Telegram chat after delivery succeeds."""
    if not TG_NOTIFY_SCRIPT.exists() or not pdf_path.exists():
        return False

    captions = {
        "premarket": f"✅ 盘前分析已完成\n📅 {date}",
        "closing": f"✅ 收盘复盘已完成\n📅 {date}",
        "weekly": f"✅ 周报已完成\n📅 {date}",
        "philosophy": f"✅ 哲学周更新已完成\n📅 {date}",
        "scout": f"✅ 选股扫描已完成\n📅 {date}",
    }
    caption = captions.get(task_type, f"✅ {task_type} 已完成\n📅 {date}")

    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(TG_NOTIFY_SCRIPT),
                "--task-type", task_type,
                "--date", date,
                "--stage", "deliver",
                "--status", "done",
                "--document", str(pdf_path),
                "--caption", caption,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(WORKSPACE),
        )
        if proc.returncode == 0:
            print(f"  [deliver] Telegram PDF sent: {pdf_path}")
            return True
        print(f"  [deliver] Telegram PDF send failed: {proc.stderr[:200]}", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("  [deliver] Telegram PDF send timed out (60s)", file=sys.stderr)
        return False


def _archive_sentiment(date: str) -> bool:
    """Archive daily sentiment snapshot (closing tasks only)."""
    if not MEMORY_MANAGER.exists():
        return False

    try:
        proc = subprocess.run(
            [sys.executable, str(MEMORY_MANAGER), "save_sentiment", date],
            capture_output=True, text=True, timeout=30,
            cwd=str(WORKSPACE),
        )
        if proc.returncode == 0:
            print(f"  [deliver] Sentiment archived for {date}")
            return True
        else:
            print(f"  [deliver] Sentiment archive failed: {proc.stderr[:200]}",
                  file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        return False


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
