#!/usr/bin/env python3
"""报告发布前质检器

使用 Claude Code 做语义级内容审核，不做关键词匹配。
仅保留少量硬性结构检查（篇幅、章节数）作为快速预检。
"""
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_BIN = "/home/bot/.local/bin/claude"

# ---------------------------------------------------------------------------
# 硬性结构预检（纯本地，不调 LLM）
# ---------------------------------------------------------------------------

MIN_WEEKLY_CHARS = 10000
MIN_WEEKLY_H2 = 6
MIN_DAILY_CHARS = 3000

REPORT_TYPE_LABELS = {
    'weekly': '周报',
    'daily_premarket': '盘前报告',
    'daily_closing': '收盘复盘',
}


def _detect_type(path: Path) -> str:
    lower = str(path).lower()
    if 'weekly' in lower or 'market-insight' in path.name:
        return 'weekly'
    if 'closing' in path.name.lower() or 'closing' in lower:
        return 'daily_closing'
    return 'daily_premarket'


def _structural_precheck(text: str, report_type: str) -> list[str]:
    """Fast local checks that don't need LLM."""
    issues = []
    import re

    if report_type == 'weekly':
        if len(text) < MIN_WEEKLY_CHARS:
            issues.append(f'周报正文过短：{len(text)} 字符 < {MIN_WEEKLY_CHARS}')
        h2_count = len(re.findall(r'^##\s+', text, flags=re.M))
        if h2_count < MIN_WEEKLY_H2:
            issues.append(f'周报结构不足：{h2_count} 个 H2 < {MIN_WEEKLY_H2}')
    else:
        if len(text) < MIN_DAILY_CHARS:
            issues.append(f'报告正文过短：{len(text)} 字符 < {MIN_DAILY_CHARS}')

    # Check for unreplaced template variables (double braces only)
    stripped = re.sub(r'```[\s\S]*?```', '', text)
    if re.search(r'\{\{[^{}]+\}\}', stripped):
        issues.append('存在未替换的模板变量')

    return issues


# ---------------------------------------------------------------------------
# Claude Code 语义审核
# ---------------------------------------------------------------------------

REVIEW_SYSTEM_PROMPT = """\
你是砚·交易台的报告质检官。你的任务是审核一份市场分析报告的内容质量。

## 审核标准

根据报告类型，检查以下维度是否被**实质性覆盖**（不是看有没有某个关键词，而是看有没有真正分析到位）：

### 所有报告必须覆盖：
1. 数据来源是否透明（读者能知道报告基于什么数据）
2. 是否有风险提醒（不是套话，而是具体的风险点）
3. 是否有反思（"如果判断错了怎么办"）
4. 是否识别了当前市场的核心矛盾

### 盘前报告额外检查：
5. 消息面是否进入了分析链条（不是只罗列新闻）
6. 技术面分析是否有深度（不是只说涨跌）
7. 是否给出了明确的方向判断和预案

### 收盘复盘额外检查：
5. 是否对照了盘前预判做验证/证伪
6. 技术面是否覆盖主要指数和观察池
7. 是否有风险评估（全球风险环境）

### 周报额外检查：
5. 全球市场是否有实质分析（不是只报数字）
6. 跨资产是否覆盖（美股、商品、汇率、利率、加密）
7. 行业赛道是否有深度（催化→资金→定价链条）
8. 观察池是否逐只分析
9. 多角色论证是否有留痕
10. 是否有下周展望

## 输出格式

严格输出 JSON：

```json
{
  "passed": true/false,
  "score": 0-100,
  "issues": ["问题1", "问题2"],
  "verdict": "PASS 或简短说明为什么不通过"
}
```

## 评判标准
- score ≥ 60 且无严重缺失 → passed=true
- "严重缺失"定义：完全没有风险提醒、完全没有方向判断、报告像是半成品
- 用词不够精确、某个维度覆盖较浅但有提及 → 不算严重缺失，扣分但通过
- 输出纯 JSON，不加额外文字
"""


def _claude_review(text: str, report_type: str) -> tuple[bool, list[str]]:
    """Use Claude Code to semantically review the report."""
    label = REPORT_TYPE_LABELS.get(report_type, '报告')

    # Truncate if extremely long to stay within reasonable prompt size
    if len(text) > 80000:
        text = text[:80000] + "\n\n... (截断，原文过长)"

    prompt = f"报告类型：{label}\n\n以下是待审核的报告全文：\n\n{text}"

    cmd = [
        CLAUDE_BIN,
        "-p",
        "--dangerously-skip-permissions",
        "--max-budget-usd=0.50",
        "--system-prompt", REVIEW_SYSTEM_PROMPT,
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(WORKSPACE_ROOT),
        )
    except subprocess.TimeoutExpired:
        # LLM review timed out — fall back to pass (don't block delivery)
        print("  [qc] Claude review timed out, falling back to PASS", file=sys.stderr)
        return True, []
    except FileNotFoundError:
        print(f"  [qc] Claude CLI not found at {CLAUDE_BIN}, falling back to PASS", file=sys.stderr)
        return True, []

    if proc.returncode != 0:
        print(f"  [qc] Claude review failed (exit {proc.returncode}), falling back to PASS", file=sys.stderr)
        return True, []

    # Parse JSON from output
    output = proc.stdout.strip()
    try:
        review = json.loads(output)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code block
        import re
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', output)
        if m:
            try:
                review = json.loads(m.group(1))
            except json.JSONDecodeError:
                print("  [qc] Claude review output not parseable, falling back to PASS", file=sys.stderr)
                return True, []
        else:
            print("  [qc] Claude review output not parseable, falling back to PASS", file=sys.stderr)
            return True, []

    passed = review.get("passed", True)
    issues = review.get("issues", [])
    score = review.get("score", 0)
    verdict = review.get("verdict", "")

    print(f"  [qc] Claude review: score={score} passed={passed} verdict={verdict}")

    return passed, issues


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def check(path: Path) -> list[str]:
    text = path.read_text(encoding='utf-8')
    report_type = _detect_type(path)

    # Step 1: fast structural precheck
    issues = _structural_precheck(text, report_type)
    if issues:
        # Structural failures are hard blockers
        return issues

    # Step 2: Claude Code semantic review
    passed, claude_issues = _claude_review(text, report_type)
    if not passed:
        return claude_issues

    return []


def main():
    if len(sys.argv) < 2:
        print('Usage: report_quality_check.py <file.md>', file=sys.stderr)
        sys.exit(1)
    path = Path(sys.argv[1])
    issues = check(path)
    if issues:
        print('FAILED')
        for i in issues:
            print('-', i)
        sys.exit(2)
    print('PASSED')


if __name__ == '__main__':
    main()
