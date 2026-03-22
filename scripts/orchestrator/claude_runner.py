#!/usr/bin/env python3
"""
Unified Claude Code invoker for the trade orchestrator.

Wraps `claude -p` CLI calls with structured input/output, timeout handling,
and error capture. All worker stages (discuss, write, review, revise) route
through this module.

Design:
    - Each call is a fresh session (no shared context between calls)
    - All data must be passed via prompt or --add-dir
    - Output is captured as text; JSON parsing is optional
    - Timeout and budget limits prevent runaway costs
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLAUDE_BIN = "/home/bot/.local/bin/claude"
WORKSPACE = Path(__file__).resolve().parents[2]
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass
class ClaudeResult:
    """Result of a Claude Code invocation."""
    success: bool
    output: str = ""
    error: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output_length": len(self.output),
            "error": self.error[:500] if self.error else "",
            "exit_code": self.exit_code,
        }


def run_claude(
    prompt: str,
    *,
    system_prompt: str = "",
    add_dirs: list[str] | None = None,
    timeout_seconds: int = 600,
    max_budget_usd: float = 2.0,
    model: str = "",
    output_format: str = "text",
    allowed_tools: list[str] | None = None,
) -> ClaudeResult:
    """Invoke Claude Code in non-interactive print mode.

    Args:
        prompt: The main prompt text (can be very long — includes data context).
        system_prompt: Optional system-level instructions.
        add_dirs: Additional directories Claude can access.
        timeout_seconds: Hard timeout for the subprocess.
        max_budget_usd: Maximum API spend for this single call.
        model: Model override (empty = default).
        output_format: "text" or "json".
        allowed_tools: Restrict available tools (e.g. ["Read", "Bash(python3:*)"]).

    Returns:
        ClaudeResult with output text or error.
    """
    cmd = [
        CLAUDE_BIN,
        "-p",
        "--bare",  # skip hooks, LSP, plugin sync — saves ~100MB + 2-3s startup
        "--dangerously-skip-permissions",
        f"--max-budget-usd={max_budget_usd}",
        f"--output-format={output_format}",
    ]

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    if model:
        cmd.extend(["--model", model])

    if add_dirs:
        for d in add_dirs:
            cmd.extend(["--add-dir", d])

    if allowed_tools is not None:
        if len(allowed_tools) == 0:
            # Empty list = disable all tools (pure text generation, lowest memory)
            cmd.extend(["--tools", ""])
        else:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])

    # Pass prompt via stdin to avoid shell argument length limits.
    # Claude Code reads from stdin when no positional prompt is given.

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        return ClaudeResult(
            success=False,
            error=f"Claude Code timed out after {timeout_seconds}s",
            exit_code=-1,
        )
    except FileNotFoundError:
        return ClaudeResult(
            success=False,
            error=f"Claude CLI not found at {CLAUDE_BIN}",
            exit_code=-2,
        )

    if proc.returncode != 0:
        return ClaudeResult(
            success=False,
            output=proc.stdout,
            error=proc.stderr[:2000] if proc.stderr else f"Exit code {proc.returncode}",
            exit_code=proc.returncode,
        )

    # Detect known error patterns that exit 0 but aren't real output
    stdout = proc.stdout.strip()
    if stdout.startswith("Error:") or stdout.startswith("error:"):
        return ClaudeResult(
            success=False,
            output=stdout,
            error=f"Claude CLI returned error in stdout: {stdout[:300]}",
            exit_code=0,
        )

    return ClaudeResult(
        success=True,
        output=proc.stdout,
        exit_code=0,
    )


def load_prompt_template(template_name: str) -> str:
    """Load a prompt template from the prompts/ directory.

    Args:
        template_name: Filename without extension (e.g. "discuss_daily").

    Returns:
        Template text content.

    Raises:
        FileNotFoundError: If template doesn't exist.
    """
    path = PROMPTS_DIR / f"{template_name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def build_data_context(artifact_map: dict[str, dict], date: str) -> str:
    """Build a data context block from normalized artifacts.

    Reads each available artifact file and concatenates them into a
    structured text block that can be included in prompts.

    Args:
        artifact_map: Dict from normalized.json "artifacts" field.
        date: Task date for reference.

    Returns:
        Formatted data context string.
    """
    sections = []
    sections.append(f"# 数据上下文 — {date}\n")

    for name, info in artifact_map.items():
        status = info.get("status", "unknown")
        output_file = info.get("output_file", "")

        if status != "ok" or not output_file:
            sections.append(f"## {name}\n状态: {status}（数据不可用）\n")
            continue

        fpath = Path(output_file)
        if not fpath.exists():
            sections.append(f"## {name}\n状态: 文件缺失 ({output_file})\n")
            continue

        try:
            content = fpath.read_text(encoding="utf-8")
            # Truncate very large outputs to keep prompt manageable
            if len(content) > 15000:
                content = content[:15000] + "\n\n... (截断，原文过长)\n"
            sections.append(f"## {name}\n```\n{content}\n```\n")
        except Exception as e:
            sections.append(f"## {name}\n读取失败: {e}\n")

    return "\n".join(sections)


def build_memory_context(date: str) -> str:
    """Build memory context by querying memory_manager.py.

    Retrieves recent signals, reviews, and reports for continuity.
    """
    memory_script = WORKSPACE / "scripts" / "memory" / "memory_manager.py"
    if not memory_script.exists():
        return "（记忆系统不可用）\n"

    sections = ["# 记忆上下文\n"]

    queries = [
        ("query_signals", "7", "近一周信号"),
        ("query_reviews", "3", "近三天复盘"),
        ("query_reports", "3", "近三天报告摘要"),
        ("status", "", "记忆状态"),
    ]

    for cmd, arg, label in queries:
        try:
            run_args = [sys.executable, str(memory_script), cmd]
            if arg:
                run_args.append(arg)
            proc = subprocess.run(
                run_args,
                capture_output=True, text=True, timeout=30,
                cwd=str(WORKSPACE),
            )
            output = proc.stdout.strip() if proc.returncode == 0 else f"查询失败: {proc.stderr[:200]}"
            if len(output) > 5000:
                output = output[:5000] + "\n... (截断)"
            sections.append(f"## {label}\n```\n{output}\n```\n")
        except subprocess.TimeoutExpired:
            sections.append(f"## {label}\n查询超时\n")

    return "\n".join(sections)


def build_watchlist_context() -> str:
    """Build watchlist context from longterm_watchlist.json.

    Returns a structured block listing all stocks/ETFs in the observation pool,
    with explicit ETF marking so the report writer knows which items need
    the ETF 10-item deep checklist (折溢价, 份额/申赎, 指数方法学, etc.).
    """
    # Use same source as QC (report_quality_check.py → load_watchlist('watchlist.json'))
    watchlist_path = WORKSPACE / "watchlist.json"
    if not watchlist_path.exists():
        return "# 当前观察池\n\n当前观察池为空。\n"

    try:
        import json as _json
        with open(watchlist_path, encoding="utf-8") as f:
            data = _json.load(f)
    except (OSError, _json.JSONDecodeError):
        return "# 当前观察池\n\n观察池文件读取失败。\n"

    stocks = data.get("stocks", [])
    if not stocks:
        return "# 当前观察池\n\n当前观察池为空。无需覆盖个股/ETF 分析。\n"

    lines = ["# 当前观察池\n"]
    has_etf = False
    for item in stocks:
        if isinstance(item, dict):
            code = str(item.get("code", ""))
            name = str(item.get("name", ""))
            ptype = str(item.get("type", "")).lower()
        else:
            code = str(item)
            name = ""
            ptype = ""

        is_etf = (
            ptype == "etf"
            or code.startswith(("5", "15", "16", "56", "58"))
            or "etf" in name.lower()
        )

        tag = " **[ETF]**" if is_etf else ""
        lines.append(f"- {code} {name}{tag}")
        if is_etf:
            has_etf = True

    if has_etf:
        lines.append("")
        lines.append(
            "**⚠️ 观察池含 ETF 标的。报告中必须对每只 ETF 覆盖以下关键词/分析维度：**\n"
            "1. 折溢价\n"
            "2. 份额 或 申赎\n"
            "3. 指数方法学 或 标的方法学 或 跟踪标的\n"
            "4. 成分 或 前十大\n"
            "缺少任一项将导致质检不通过。"
        )

    return "\n".join(lines) + "\n"
